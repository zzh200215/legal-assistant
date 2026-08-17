import io
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.document import Document, DocumentChunk
from app.models.user import User
from app.services.documents.document_parsing import (
    DocumentParsePermanentError,
    _build_segment,
    _build_visual_summary,
    _extract_segments_from_pdf,
    _extract_visual_evidence,
    _extract_visual_evidence_with_positions,
    _looks_like_low_quality_text,
    _prepare_chunks_for_indexing,
    _render_table_from_ocr_data,
    _split_markdown_sections,
    _split_text,
)
from app.services.documents.document_parsing import _extract_segments
from app.services.documents.document_service import document_service


class DocumentChunkingTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        Base.metadata.create_all(bind=engine)
        self.db = self.SessionLocal()
        self.user = User(
            username="doc_tester",
            email="doc_tester@example.com",
            hashed_password="hashed",
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

    def tearDown(self):
        self.db.close()

    def test_split_markdown_sections_tracks_heading_path(self):
        md_text = "# 一级标题\n内容 A\n## 二级标题\n- 待办 1\n- 待办 2\n"

        segments = _split_markdown_sections(md_text)

        self.assertEqual(segments[0]["section_path"], ["一级标题"])
        self.assertEqual(segments[1]["section_path"], ["一级标题", "二级标题"])

    def test_split_text_preserves_segment_metadata(self):
        segments = [
            _build_segment(
                text="| 列1 | 列2 |\n| A | B |",
                page_number=2,
                section_title="资源表",
                section_path=["交付计划", "资源表"],
                segment_type="table",
            )
        ]

        chunks = _split_text(segments, chunk_size=80, chunk_overlap=0)

        self.assertEqual(chunks[0]["section_path"], ["交付计划", "资源表"])
        self.assertEqual(chunks[0]["segment_type"], "table")
        self.assertTrue(chunks[0]["table_like"])

    def test_split_text_reads_config_chunk_size_when_not_explicit(self):
        """RAG③：不传 chunk_size/overlap 时读 settings.RAG_CHUNK_SIZE/_OVERLAP；显式传参优先。"""
        from app.core.config import get_settings
        s = get_settings()
        long_text = "法律文书测试内容" * 30  # 270 字
        with patch.object(s, "RAG_CHUNK_SIZE", 80), patch.object(s, "RAG_CHUNK_OVERLAP", 0):
            chunks = _split_text(long_text)
        self.assertGreater(len(chunks), 1)
        # 短块合并允许末尾碎块并入前块，略超 chunk_size
        self.assertTrue(all(len(c["content"]) <= 120 for c in chunks))
        # 显式传参（测试/eval）仍优先于配置
        chunks2 = _split_text(long_text, chunk_size=800, chunk_overlap=100)
        self.assertGreater(len(chunks2[0]["content"]), 80)

    def test_normalize_text_strips_page_number_lines(self):
        """清洗：剥离页眉/页脚中的纯页码行。"""
        from app.services.documents.document_parsing import _normalize_text
        cleaned = _normalize_text("合同正文\n第 3 页\n甲方义务\n- 12 -\n乙方权利")
        self.assertNotIn("第 3 页", cleaned)
        self.assertNotIn("- 12 -", cleaned)
        self.assertIn("合同正文", cleaned)
        self.assertIn("甲方义务", cleaned)
        self.assertIn("乙方权利", cleaned)

    def test_normalize_text_cleans_control_chars_and_spaces(self):
        """清洗：去除控制字符、全角空格转半角并折叠连续空格。"""
        from app.services.documents.document_parsing import _normalize_text
        cleaned = _normalize_text("a\x00b　c  d\x1f")
        self.assertEqual(cleaned, "ab c d")

    def test_split_keeps_table_rows_intact(self):
        """分块：表格段只在行边界切分，绝不从行中间断开（含句号的行不被切断）。"""
        row = "甲方应支付货款。乙方验收合格后十个工作日内付清。"
        table_text = "\n".join(f"| {row}{i} |" for i in range(4))
        segments = [
            _build_segment(
                text=table_text, page_number=None, section_title="付款表",
                section_path=["商务条款", "付款表"], segment_type="table",
            )
        ]
        chunks = _split_text(segments, chunk_size=60, chunk_overlap=0)
        self.assertGreaterEqual(len(chunks), 1)
        for chunk in chunks:
            for line in chunk["content"].split("\n"):
                line = line.strip()
                if not line:
                    continue
                self.assertTrue(line.startswith("| ") and line.endswith(" |"),
                                f"表格行被切断: {line!r}")

    def test_split_merges_short_fragments(self):
        """分块：过短片段并入前一块，避免碎块。"""
        from app.services.documents.document_parsing import _merge_short_chunks
        chunks = [
            {"chunk_index": 0, "content": "A" * 100, "section_title": "s", "section_path": ["s"]},
            {"chunk_index": 1, "content": "B" * 10, "section_title": "s", "section_path": ["s"]},
            {"chunk_index": 2, "content": "C" * 90, "section_title": "s", "section_path": ["s"]},
        ]
        merged = _merge_short_chunks(chunks, min_length=40)
        self.assertEqual(len(merged), 2)
        self.assertIn("A" * 100, merged[0]["content"])
        self.assertIn("B" * 10, merged[0]["content"])
        self.assertIn("C" * 90, merged[1]["content"])

    def test_split_text_derives_visual_tags_for_signature_ocr_segment(self):
        segments = [
            _build_segment(
                text="本页包含甲方签字和公司公章。",
                page_number=8,
                section_title="第 8 页",
                section_path=["合同附件", "第 8 页"],
                segment_type="page_ocr",
            )
        ]

        chunks = _split_text(segments, chunk_size=80, chunk_overlap=0)

        self.assertIn("seal_present", chunks[0]["visual_tags"])
        self.assertIn("signature_present", chunks[0]["visual_tags"])
        self.assertGreater(chunks[0]["ocr_quality"], 0.4)

    def test_cross_document_conflicts_require_matching_subject_and_source_locator(self):
        contract = Document(
            user_id=self.user.id,
            title="实施合同",
            file_path="uploads/contract.md",
            file_type="md",
            status="indexed",
        )
        project_plan = Document(
            user_id=self.user.id,
            title="项目计划",
            file_path="uploads/plan.md",
            file_type="md",
            status="indexed",
        )
        self.db.add_all([contract, project_plan])
        self.db.commit()
        self.db.add_all(
            [
                DocumentChunk(
                    document_id=contract.id,
                    chunk_index=0,
                    page_number=3,
                    section_title="项目里程碑",
                    content="项目上线日期为 2026 年 8 月 1 日。",
                ),
                DocumentChunk(
                    document_id=project_plan.id,
                    chunk_index=0,
                    page_number=2,
                    section_title="上线安排",
                    content="项目上线时间调整为 2026 年 8 月 15 日。",
                ),
            ]
        )
        self.db.commit()

        analyses = [
            {
                "document_id": contract.id,
                "title": contract.title,
                "structured_fields": {
                    "dates": [
                        {
                            "value": "2026 年 8 月 1 日",
                            "normalized_date": "2026-08-01",
                            "description": "项目上线日期",
                            "source_text": "项目上线日期为 2026 年 8 月 1 日。",
                        }
                    ],
                    "amounts": [
                        {
                            "value": "268 万元",
                            "amount": "268 万元",
                            "description": "合同总金额",
                            "source_text": "合同总金额为 268 万元。",
                        }
                    ],
                    "owners": [],
                },
            },
            {
                "document_id": project_plan.id,
                "title": project_plan.title,
                "structured_fields": {
                    "dates": [
                        {
                            "value": "2026 年 8 月 15 日",
                            "normalized_date": "2026-08-15",
                            "description": "项目上线时间",
                            "source_text": "项目上线时间调整为 2026 年 8 月 15 日。",
                        }
                    ],
                    "amounts": [
                        {
                            "value": "268 万元",
                            "amount": "268 万元",
                            "description": "合同总金额",
                            "source_text": "未出现在当前片段中的金额依据",
                        }
                    ],
                    "owners": [],
                },
            },
        ]

        result = document_service._detect_cross_document_conflicts(analyses, self.db)

        self.assertEqual(result["facts_extracted"], 4)
        self.assertEqual(result["comparable_pairs"], 2)
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["field_type"], "dates")
        self.assertEqual(conflict["source_a"]["page_number"], 3)
        self.assertEqual(conflict["source_b"]["section_title"], "上线安排")
        self.assertTrue(conflict["evidence_complete"])
        self.assertEqual(conflict["status"], "confirmed")

    def test_cross_document_conflict_without_locator_is_not_confirmed(self):
        result = document_service._detect_cross_document_conflicts(
            [
                {
                    "document_id": 101,
                    "title": "合同",
                    "structured_fields": {
                        "dates": [
                            {
                                "value": "2026 年 8 月 1 日",
                                "normalized_date": "2026-08-01",
                                "description": "验收日期",
                                "source_text": "合同约定验收日期为 2026 年 8 月 1 日。",
                            }
                        ],
                        "amounts": [],
                        "owners": [],
                    },
                },
                {
                    "document_id": 102,
                    "title": "会议纪要",
                    "structured_fields": {
                        "dates": [
                            {
                                "value": "2026 年 8 月 8 日",
                                "normalized_date": "2026-08-08",
                                "description": "验收日期",
                                "source_text": "会议中口头确认验收日期为 2026 年 8 月 8 日。",
                            }
                        ],
                        "amounts": [],
                        "owners": [],
                    },
                },
            ],
            self.db,
        )

        self.assertEqual(len(result["conflicts"]), 1)
        self.assertFalse(result["conflicts"][0]["evidence_complete"])
        self.assertEqual(result["conflicts"][0]["status"], "needs_evidence")

    def test_prepare_chunks_for_indexing_preserves_structure_metadata(self):
        chunks = [
            {
                "chunk_index": 0,
                "content": "付款日期 | 付款金额",
                "section_title": "付款计划",
                "section_path": ["商务条款", "付款计划"],
                "segment_type": "table",
                "table_like": True,
                "visual_tags": ["table_visual", "table_dense"],
                "ocr_quality": 0.88,
            }
        ]

        prepared = _prepare_chunks_for_indexing(88, chunks)

        self.assertEqual(prepared[0]["embedding_id"], "doc88_chunk0")
        self.assertEqual(prepared[0]["section_path"], ["商务条款", "付款计划"])
        self.assertEqual(prepared[0]["segment_type"], "table")
        self.assertTrue(prepared[0]["table_like"])
        self.assertEqual(prepared[0]["visual_tags"], ["table_visual", "table_dense"])
        self.assertEqual(prepared[0]["ocr_quality"], 0.88)
        self.assertIn("[视觉摘要]", prepared[0]["index_content"])
        self.assertIn("表格视觉", prepared[0]["index_content"])
        self.assertIn("visual_evidence", prepared[0])

    def test_build_visual_summary_formats_visual_metadata(self):
        summary = _build_visual_summary(
            visual_tags=["ocr", "scanned_page", "seal_present", "signature_present"],
            segment_type="page_ocr",
            page_number=12,
            ocr_quality=0.91,
            section_title="第 12 页",
        )

        self.assertIn("[视觉摘要]", summary)
        self.assertIn("来源: OCR", summary)
        self.assertIn("检测到公章", summary)
        self.assertIn("检测到签字", summary)

    def test_extract_visual_evidence_picks_signature_and_seal_lines(self):
        evidence = _extract_visual_evidence(
            "合同正文第一页\n甲方代表签字：张三\n乙方已盖章确认\n其他说明",
            visual_tags=["seal_present", "signature_present"],
        )

        self.assertIn("甲方代表签字：张三", evidence)
        self.assertIn("乙方已盖章确认", evidence)

    def test_extract_visual_evidence_with_positions_returns_bottom_region(self):
        evidence, region = _extract_visual_evidence_with_positions(
            {
                "text": ["合同正文", "甲方代表签字", "乙方盖章确认"],
                "left": [10, 10, 10],
                "top": [10, 160, 190],
                "width": [40, 60, 60],
                "height": [12, 12, 12],
                "conf": ["95", "95", "95"],
            },
            visual_tags=["seal_present", "signature_present"],
        )

        self.assertIn("甲方代表签字", evidence)
        self.assertEqual(region, "bottom")

    def test_upload_indexes_chunks_with_structure_metadata(self):
        upload = type(
            "UploadFileStub",
            (),
            {
                "filename": "plan.md",
                "file": io.BytesIO(b"# title\ncontent"),
            },
        )()
        fake_segments = [
            _build_segment(
                text="| 日期 | 金额 |\n| 2026-07-01 | 100万 |",
                page_number=None,
                section_title="付款计划",
                section_path=["商务条款", "付款计划"],
                segment_type="table",
            )
        ]
        captured = {}

        def fake_index_document(document_id, chunks, user_id=None, knowledge_base_id=None, document_status=None):
            captured["document_id"] = document_id
            captured["chunks"] = chunks
            captured["user_id"] = user_id
            captured["knowledge_base_id"] = knowledge_base_id
            captured["document_status"] = document_status

        with patch("app.services.documents.document_service._extract_segments", return_value=fake_segments), patch(
            "app.services.documents.document_service.rag_service.index_document",
            side_effect=fake_index_document,
        ):
            doc = document_service.upload(upload, user_id=self.user.id, db=self.db, async_mode=False)

        self.assertEqual(captured["document_id"], doc.id)
        self.assertEqual(captured["user_id"], self.user.id)
        self.assertEqual(captured["knowledge_base_id"], doc.knowledge_base_id)
        self.assertEqual(captured["document_status"], "indexed")
        self.assertEqual(captured["chunks"][0]["section_path"], ["商务条款", "付款计划"])
        self.assertEqual(captured["chunks"][0]["segment_type"], "table")
        self.assertTrue(captured["chunks"][0]["table_like"])
        self.assertIn("table_visual", captured["chunks"][0]["visual_tags"])
        self.assertTrue(captured["chunks"][0]["embedding_id"].startswith(f"doc{doc.id}_chunk"))
        stored_chunk = self.db.query(type(doc).chunks.property.mapper.class_).filter_by(document_id=doc.id, chunk_index=0).first()
        self.assertEqual(stored_chunk.section_path, "商务条款 > 付款计划")
        self.assertEqual(stored_chunk.segment_type, "table")
        self.assertTrue(stored_chunk.table_like)
        self.assertIn("table_visual", stored_chunk.visual_tags)

    def test_upload_succeeds_when_indexing_is_unavailable(self):
        upload = type(
            "UploadFileStub",
            (),
            {
                "filename": "plan.md",
                "file": io.BytesIO(b"# title\ncontent"),
            },
        )()
        fake_segments = [
            _build_segment(
                text="content",
                page_number=None,
                section_title="title",
                section_path=["title"],
                segment_type="paragraph",
            )
        ]

        with patch("app.services.documents.document_parsing._extract_segments", return_value=fake_segments), patch(
            "app.services.documents.document_service.rag_service.index_document",
            side_effect=RuntimeError("embedding service unavailable"),
        ):
            doc = document_service.upload(upload, user_id=self.user.id, db=self.db, async_mode=False)

        self.assertEqual(doc.status, "parsed")
        stored = self.db.get(type(doc), doc.id)
        self.assertEqual(stored.status, "parsed")

    def test_extract_segments_supports_image_via_ocr(self):
        with patch("app.services.documents.document_parsing._ocr_image_to_table_text", return_value=None), patch(
            "app.services.documents.document_parsing._ocr_image_to_text",
            return_value="图片里的付款条款",
        ):
            segments = _extract_segments("data/uploads/contract.png", "png")

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["segment_type"], "image_ocr")
        self.assertIn("付款条款", segments[0]["text"])
        self.assertIn("ocr", segments[0]["visual_tags"])
        self.assertGreater(segments[0]["ocr_quality"], 0.3)

    def test_extract_image_raises_when_ocr_is_unavailable(self):
        with patch(
            "app.services.documents.document_parsing._ocr_image_to_text",
            side_effect=DocumentParsePermanentError("当前环境未启用 OCR，无法解析图片或扫描 PDF。"),
        ):
            with self.assertRaises(DocumentParsePermanentError):
                _extract_segments("data/uploads/scan.png", "png")

    def test_extract_segments_from_pdf_uses_ocr_for_scanned_pages(self):
        class FakePageImage:
            def __init__(self):
                self.original = object()

        class FakePage:
            def extract_text(self):
                return ""

            def to_image(self, resolution=150):
                return FakePageImage()

        class FakePdf:
            pages = [FakePage()]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch("app.services.documents.document_parsing.pdfplumber.open", return_value=FakePdf()), patch(
            "app.services.documents.document_parsing._ocr_image_to_table_text",
            return_value=None,
        ), patch(
            "app.services.documents.document_parsing._ocr_image_to_text",
            return_value="扫描页中的合同文本",
        ):
            segments = _extract_segments_from_pdf("uploads/scanned.pdf")

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["segment_type"], "page_ocr")
        self.assertEqual(segments[0]["page_number"], 1)
        self.assertIn("ocr", segments[0]["visual_tags"])

    def test_extract_segments_from_pdf_falls_back_to_ocr_for_low_quality_text(self):
        class FakePageImage:
            def __init__(self):
                self.original = object()

        class FakePage:
            def extract_text(self):
                return "...."

            def to_image(self, resolution=150):
                return FakePageImage()

        class FakePdf:
            pages = [FakePage()]

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch("app.services.documents.document_parsing.pdfplumber.open", return_value=FakePdf()), patch(
            "app.services.documents.document_parsing._ocr_image_to_table_text",
            return_value=None,
        ), patch(
            "app.services.documents.document_parsing._ocr_image_to_text",
            return_value="扫描页中的合同正文",
        ):
            segments = _extract_segments_from_pdf("uploads/low-quality.pdf")

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["segment_type"], "page_ocr")
        self.assertIn("合同正文", segments[0]["text"])
        self.assertIn("ocr", segments[0]["visual_tags"])

    def test_extract_segments_supports_table_screenshot_via_ocr_layout(self):
        with patch(
            "app.services.documents.document_parsing._ocr_image_to_table_text",
            return_value="| 日期 | 金额 |\n| 2026-07-01 | 100万 |",
        ):
            segments = _extract_segments("uploads/table.png", "png")

        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["segment_type"], "table")
        self.assertIn("| 日期 | 金额 |", segments[0]["text"])
        self.assertIn("table_visual", segments[0]["visual_tags"])

    def test_render_table_from_ocr_data_reconstructs_rows_and_columns(self):
        ocr_data = {
            "text": ["日期", "金额", "2026-07-01", "100万", "2026-08-01", "80万"],
            "left": [10, 120, 10, 120, 10, 120],
            "top": [10, 10, 40, 40, 70, 70],
            "width": [30, 30, 60, 35, 60, 30],
            "height": [12, 12, 12, 12, 12, 12],
            "conf": ["95", "95", "90", "90", "92", "92"],
        }

        table_text = _render_table_from_ocr_data(ocr_data)

        self.assertEqual(
            table_text,
            "| 日期 | 金额 |\n| 2026-07-01 | 100万 |\n| 2026-08-01 | 80万 |",
        )

    def test_looks_like_low_quality_text_flags_short_symbol_heavy_text(self):
        self.assertTrue(_looks_like_low_quality_text("...."))
        self.assertTrue(_looks_like_low_quality_text("a1"))
        self.assertFalse(_looks_like_low_quality_text("甲方应于2026年7月1日前支付首付款100万元。"))


if __name__ == "__main__":
    unittest.main()
