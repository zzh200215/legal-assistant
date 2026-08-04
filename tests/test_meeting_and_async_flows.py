import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace
import io
import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.services.document_service import DocumentService
from app.models.meeting import Meeting, MeetingSummary
from app.models.document import Document, DocumentChunk
from app.models.user import User
from app.services.meeting_service import meeting_service


class MeetingServiceFlowTests(unittest.TestCase):
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
            username="meeting_tester",
            email="meeting_tester@example.com",
            hashed_password="secret",
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)

        self.meeting = Meeting(
            user_id=self.user.id,
            title="项目推进会",
            transcript="王敏负责本周五前提交风险清单，李雷负责同步客户更新时间。",
            status="pending",
        )
        self.db.add(self.meeting)
        self.db.commit()
        self.db.refresh(self.meeting)

    def tearDown(self):
        self.db.close()

    def test_meeting_summary_is_serialized_with_expected_sections(self):
        fake_summary = {
            "theme": "项目推进",
            "summary": "本次会议明确了风险与客户同步安排。",
            "topics": [{"topic": "风险清单", "description": "本周整理风险", "duration_hint": "前半段", "key_points": ["周五前提交"]}],
            "decisions": [{"content": "本周五前提交风险清单", "status": "confirmed", "evidence": "王敏负责本周五前提交风险清单"}],
            "action_items": [{"title": "提交风险清单", "assignee": "王敏", "due_date": "2026-06-26", "priority": "high"}],
            "risks": [{"title": "客户沟通滞后", "description": "客户更新时间需要及时同步"}],
        }

        with patch("app.services.meeting_service.analysis_service.summarize_meeting", new=AsyncMock(return_value=fake_summary)):
            stored = self._run_summarize()

        self.assertEqual(stored["theme"], "项目推进")
        self.assertEqual(stored["summary"], "本次会议明确了风险与客户同步安排。")
        self.assertEqual(stored["topics"][0]["topic"], "风险清单")
        self.assertEqual(stored["decisions"][0]["status"], "confirmed")
        self.assertEqual(stored["action_items"][0]["assignee"], "王敏")
        self.assertEqual(stored["risks"][0]["title"], "客户沟通滞后")

    def test_meeting_extract_tasks_creates_traceable_tasks(self):
        summary = MeetingSummary(
            meeting_id=self.meeting.id,
            theme="项目推进",
            summary="",
            topics="[]",
            decisions="[]",
            action_items='[{"title":"提交风险清单","description":"整理并提交项目风险","assignee":"王敏","due_date":"2026-06-26","priority":"high"}]',
            risks="[]",
        )
        self.db.add(summary)
        self.db.commit()

        tasks = meeting_service.extract_tasks(self.meeting.id, self.user.id, self.db)

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].title, "提交风险清单")
        self.assertEqual(tasks[0].source_type, "meeting")
        self.assertEqual(tasks[0].source_id, self.meeting.id)
        self.assertEqual(tasks[0].assignee, "王敏")

    def test_meeting_summary_result_matches_async_task_contract(self):
        fake_summary = {
            "theme": "项目推进",
            "summary": "需要同步客户时间表并跟踪风险。",
            "topics": [],
            "decisions": [{"content": "同步客户时间表", "status": "confirmed", "evidence": "李雷负责同步客户更新时间"}],
            "action_items": [{"title": "同步客户时间表", "assignee": "李雷", "priority": "medium"}],
            "risks": [],
        }

        with patch("app.services.meeting_service.analysis_service.summarize_meeting", new=AsyncMock(return_value=fake_summary)):
            result = self._run_summarize()

        self.assertEqual(result["theme"], "项目推进")
        self.assertEqual(result["decisions"][0]["content"], "同步客户时间表")
        self.assertEqual(result["action_items"][0]["assignee"], "李雷")

    def test_create_meeting_from_uploaded_image_uses_ocr_text(self):
        upload = type(
            "UploadFileStub",
            (),
            {
                "filename": "meeting-note.png",
                "file": io.BytesIO(b"fake-image"),
            },
        )()

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "app.services.meeting_service.UPLOAD_DIR",
            Path(tmpdir),
        ), patch(
            "app.services.meeting_service.extract_file_text",
            return_value="王敏负责整理周报，李雷负责同步客户。",
        ):
            meeting = meeting_service.create_from_uploaded_image(
                title="周会纪要图片",
                file=upload,
                user_id=self.user.id,
                db=self.db,
            )

        self.assertEqual(meeting.title, "周会纪要图片")
        self.assertIn("王敏负责整理周报", meeting.transcript)
        self.assertEqual(meeting.status, "pending")

    def test_create_meeting_from_uploaded_image_rejects_invalid_type(self):
        upload = type(
            "UploadFileStub",
            (),
            {
                "filename": "meeting.zip",
                "file": io.BytesIO(b"fake-binary"),
            },
        )()

        with self.assertRaises(ValueError):
            meeting_service.create_from_uploaded_image(
                title="无效附件",
                file=upload,
                user_id=self.user.id,
                db=self.db,
            )

    def test_create_meeting_from_uploaded_audio_uses_asr_when_transcript_is_empty(self):
        upload = type(
            "UploadFileStub",
            (),
            {"filename": "weekly-sync.mp3", "file": io.BytesIO(b"fake-audio")},
        )()
        transcription = type(
            "TranscriptionStub",
            (),
            {
                "text": "王敏负责提交风险清单。",
                "segments": [{"start_seconds": 0.0, "end_seconds": 3.2, "text": "王敏负责提交风险清单。"}],
            },
        )()

        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "app.services.meeting_service.UPLOAD_DIR", Path(tmpdir)
        ), patch(
            "app.services.meeting_service.meeting_transcription_service.transcribe",
            return_value=transcription,
        ):
            meeting = meeting_service.create_from_uploaded_audio(
                title="周会音频",
                file=upload,
                transcript_text=None,
                user_id=self.user.id,
                db=self.db,
            )

        self.assertEqual(meeting.transcript_source, "asr")
        self.assertEqual(meeting.transcript, transcription.text)
        self.assertEqual(meeting_service.get_transcript(meeting)["segments"][0]["end_seconds"], 3.2)

    def _run_summarize(self) -> dict:
        import asyncio

        summary_model = asyncio.run(meeting_service.summarize(self.meeting.id, self.db, user_id=self.user.id))
        return meeting_service.serialize_summary(summary_model)


class DocumentCompareFlowTests(unittest.TestCase):
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
            username="doc_compare_tester",
            email="doc_compare_tester@example.com",
            hashed_password="secret",
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.doc1 = Document(
            user_id=self.user.id,
            title="合同A.docx",
            file_path="uploads/doc_a.docx",
            file_type="docx",
            status="indexed",
        )
        self.doc2 = Document(
            user_id=self.user.id,
            title="合同B.docx",
            file_path="uploads/doc_b.docx",
            file_type="docx",
            status="indexed",
        )
        self.db.add_all([self.doc1, self.doc2])
        self.db.commit()
        self.db.refresh(self.doc1)
        self.db.refresh(self.doc2)
        self.service = DocumentService()

    def tearDown(self):
        self.db.close()

    def test_document_compare_returns_normalized_structure(self):
        analyses = [
            {
                "summary": "合同 A 付款节点较早。",
                "risks": [{"title": "付款压力", "description": "首付款比例较高"}],
                "todos": [{"title": "复核付款计划", "description": "确认现金流安排"}],
                "clauses": [],
                "references": [{"label": "文档片段 1"}],
            },
            {
                "summary": "合同 B 交付周期更长。",
                "risks": [{"title": "交付延期", "description": "验收节点较多"}],
                "todos": [{"title": "确认验收标准", "description": "补充交付清单"}],
                "clauses": [],
                "references": [{"label": "文档片段 2"}],
            },
        ]
        comparison_payload = {
            "overview": "合同 A 付款更激进，合同 B 交付约束更多。",
            "common_points": ["都包含验收与付款条款"],
            "differences": [{"title": "付款安排", "detail": "合同 A 首付款更高"}],
            "risk_delta": [{"title": "风险重点", "detail": "合同 B 更偏交付延期风险", "severity": "high"}],
            "action_suggestions": ["优先复核付款与验收条款"],
        }

        async def fake_analyze(document_id, db, user_id=None, max_length=500, **kwargs):
            return analyses[0] if document_id == self.doc1.id else analyses[1]

        analysis_service = __import__("app.services.analysis_service", fromlist=["analysis_service"]).analysis_service

        with patch.object(self.service, "analyze", side_effect=fake_analyze), patch.object(
            analysis_service,
            "_extract_json_object",
            new=AsyncMock(return_value=comparison_payload),
        ):
            import asyncio

            result = asyncio.run(
                self.service.compare(
                    [self.doc1.id, self.doc2.id],
                    self.db,
                    user_id=self.user.id,
                )
            )

        self.assertEqual(result["comparison"]["comparison_type"], "document_diff")
        self.assertEqual(result["comparison"]["document_count"], 2)
        self.assertEqual(result["comparison"]["differences"][0]["title"], "付款安排")
        self.assertEqual(result["comparison"]["risk_delta"][0]["severity"], "high")
        self.assertEqual(result["summary_cards"][0]["risk_count"], 1)
        self.assertEqual(result["summary_cards"][1]["todo_count"], 1)

    def test_document_compare_rejects_more_than_five_documents(self):
        import asyncio

        with self.assertRaises(ValueError):
            asyncio.run(
                self.service.compare(
                    [1, 2, 3, 4, 5, 6],
                    self.db,
                    user_id=self.user.id,
                )
            )

    def test_document_analyze_returns_partial_result_when_one_stage_fails(self):
        async def fake_summary(text, max_length=500, user_id=None):
            return "合同摘要"

        async def fake_risks(text, user_id=None):
            raise RuntimeError("risk extraction timeout")

        async def fake_todos(text, user_id=None):
            return [{"title": "复核付款计划", "description": "确认现金流安排"}]

        async def fake_clauses(text, user_id=None):
            return [{"title": "付款条款", "description": "首付款 30%"}]

        async def fake_fields(text, user_id=None):
            return {
                "dates": [{"value": "2026-06-30", "normalized_date": "2026-06-30", "description": "验收截止日"}],
                "amounts": [],
                "owners": [],
                "risk_clauses": [],
            }

        with patch("app.services.document_service._extract_text", return_value="这是一份测试文档。"), patch(
            "app.services.document_service.analysis_service.summarize_document",
            new=AsyncMock(side_effect=fake_summary),
        ), patch(
            "app.services.document_service.analysis_service.extract_document_risks",
            new=AsyncMock(side_effect=fake_risks),
        ), patch(
            "app.services.document_service.analysis_service.extract_document_todos",
            new=AsyncMock(side_effect=fake_todos),
        ), patch(
            "app.services.document_service.analysis_service.extract_document_clauses",
            new=AsyncMock(side_effect=fake_clauses),
        ), patch(
            "app.services.document_service.analysis_service.extract_document_fields",
            new=AsyncMock(side_effect=fake_fields),
        ):
            import asyncio

            result = asyncio.run(
                self.service.analyze(
                    self.doc1.id,
                    self.db,
                    user_id=self.user.id,
                )
            )

        self.assertEqual(result["analysis_status"], "partial")
        self.assertEqual(result["summary"], "合同摘要")
        self.assertEqual(result["risks"], [])
        self.assertEqual(len(result["todos"]), 1)
        self.assertEqual(len(result["clauses"]), 1)
        self.assertEqual(result["structured_fields"]["dates"][0]["value"], "2026-06-30")
        self.assertEqual(result["analysis_warnings"][0]["stage"], "risks")

    def test_document_analyze_returns_structured_fields(self):
        async def fake_summary(text, max_length=500, user_id=None):
            return "合同摘要"

        async def fake_risks(text, user_id=None):
            return []

        async def fake_todos(text, user_id=None):
            return []

        async def fake_clauses(text, user_id=None):
            return []

        async def fake_fields(text, user_id=None):
            return {
                "dates": [{"value": "2026年7月1日", "normalized_date": "2026-07-01", "description": "首付款日期", "source_text": "首付款应于2026年7月1日前支付"}],
                "amounts": [{"value": "100万元", "amount": "1000000", "currency": "CNY", "description": "首付款金额", "source_text": "首付款金额为100万元"}],
                "owners": [{"name": "王敏", "role": "项目经理", "responsibility": "提交风险清单", "source_text": "王敏负责提交风险清单"}],
                "risk_clauses": [{"title": "延期违约", "description": "延期交付需承担违约责任", "severity": "high", "source_text": "延期交付的，需承担违约责任", "suggestion": "补充责任上限"}],
            }

        with patch("app.services.document_service._extract_text", return_value="这是一份测试文档。"), patch(
            "app.services.document_service.analysis_service.summarize_document",
            new=AsyncMock(side_effect=fake_summary),
        ), patch(
            "app.services.document_service.analysis_service.extract_document_risks",
            new=AsyncMock(side_effect=fake_risks),
        ), patch(
            "app.services.document_service.analysis_service.extract_document_todos",
            new=AsyncMock(side_effect=fake_todos),
        ), patch(
            "app.services.document_service.analysis_service.extract_document_clauses",
            new=AsyncMock(side_effect=fake_clauses),
        ), patch(
            "app.services.document_service.analysis_service.extract_document_fields",
            new=AsyncMock(side_effect=fake_fields),
        ):
            import asyncio

            result = asyncio.run(self.service.analyze(self.doc1.id, self.db, user_id=self.user.id))

        self.assertEqual(result["analysis_status"], "success")
        self.assertEqual(result["structured_fields"]["amounts"][0]["currency"], "CNY")
        self.assertEqual(result["structured_fields"]["owners"][0]["name"], "王敏")
        self.assertEqual(result["structured_fields"]["risk_clauses"][0]["severity"], "high")
        self.assertTrue(any(item["label"].startswith("金额依据") for item in result["references"]))

    def test_document_analyze_visual_calls_multimodal_service_for_image_document(self):
        image_doc = Document(
            user_id=self.user.id,
            title="scan.png",
            file_path=str(Path(__file__).resolve()),
            file_type="png",
            status="indexed",
        )
        self.db.add(image_doc)
        self.db.commit()
        self.db.refresh(image_doc)

        with patch(
            "app.services.document_service._file_to_data_url",
            return_value="data:image/png;base64,abc",
        ), patch(
            "app.services.document_service.llm_service.generate_with_images",
            new=AsyncMock(return_value="图片中包含签字和公章。"),
        ):
            import asyncio

            result = asyncio.run(
                self.service.analyze_visual(
                    image_doc.id,
                    "请识别图中的签字和公章",
                    self.db,
                    user_id=self.user.id,
                )
            )

        self.assertEqual(result["document_id"], image_doc.id)
        self.assertEqual(result["file_type"], "png")
        self.assertIn("签字和公章", result["analysis"])

    def test_document_analyze_visual_calls_multimodal_service_for_pdf_document(self):
        pdf_doc = Document(
            user_id=self.user.id,
            title="scan-contract.pdf",
            file_path=str(Path(__file__).resolve()),
            file_type="pdf",
            status="indexed",
        )
        self.db.add(pdf_doc)
        self.db.commit()
        self.db.refresh(pdf_doc)

        with patch(
            "app.services.document_service._file_to_data_url",
            return_value="data:application/pdf;base64,abc",
        ), patch(
            "app.services.document_service.llm_service.generate_with_images",
            new=AsyncMock(return_value="第2页下部包含签字区域，并检测到公章。"),
        ) as mock_generate:
            import asyncio

            result = asyncio.run(
                self.service.analyze_visual(
                    pdf_doc.id,
                    "请识别合同扫描件中的签字和公章位置",
                    self.db,
                    user_id=self.user.id,
                )
            )

        self.assertEqual(result["document_id"], pdf_doc.id)
        self.assertEqual(result["file_type"], "pdf")
        self.assertIn("签字区域", result["analysis"])
        self.assertEqual(result["image_count"], 1)
        mock_generate.assert_awaited_once()

    def test_document_ask_injects_visual_analysis_for_image_documents(self):
        image_doc = Document(
            user_id=self.user.id,
            title="scan.png",
            file_path="uploads/scan.png",
            file_type="png",
            status="indexed",
        )
        self.db.add(image_doc)
        self.db.commit()
        self.db.refresh(image_doc)

        captured = {}

        def fake_answer(question, document_id=None, user_id=None, **kwargs):
            captured["question"] = question
            return {
                "answer": "该页包含签字和公章。",
                "citations": [],
                "confidence": 0.82,
                "can_answer": True,
                "hit_chunks": [],
                "latency_ms": 10,
            }

        with patch.object(
            self.service,
            "analyze_visual",
            new=AsyncMock(
                return_value={
                    "document_id": image_doc.id,
                    "title": image_doc.title,
                    "file_type": "png",
                    "analysis": "图片下方包含签字和公章。",
                    "image_count": 1,
                }
            ),
        ), patch(
            "app.services.document_service.agentic_rag_service.answer",
            side_effect=fake_answer,
        ), patch(
            "app.services.document_service.document_qa_service.record",
            return_value=SimpleNamespace(
                id=1,
                feedback_value=None,
                feedback_status=None,
            ),
        ):
            result = self.service.ask(image_doc.id, "这页有没有签字", self.db, user_id=self.user.id)

        self.assertIn("补充视觉分析线索", captured["question"])
        self.assertIn("图片下方包含签字和公章", captured["question"])
        self.assertTrue(result["can_answer"])

    def test_document_ask_falls_back_when_visual_analysis_fails(self):
        image_doc = Document(
            user_id=self.user.id,
            title="scan.png",
            file_path="uploads/scan.png",
            file_type="png",
            status="indexed",
        )
        self.db.add(image_doc)
        self.db.commit()
        self.db.refresh(image_doc)

        captured = {}

        def fake_answer(question, document_id=None, user_id=None, **kwargs):
            captured["question"] = question
            return {
                "answer": "无法确认。",
                "citations": [],
                "confidence": 0.2,
                "can_answer": False,
                "hit_chunks": [],
                "latency_ms": 8,
            }

        with patch.object(
            self.service,
            "analyze_visual",
            new=AsyncMock(side_effect=RuntimeError("vision unavailable")),
        ), patch(
            "app.services.document_service.agentic_rag_service.answer",
            side_effect=fake_answer,
        ), patch(
            "app.services.document_service.document_qa_service.record",
            return_value=SimpleNamespace(
                id=1,
                feedback_value=None,
                feedback_status=None,
            ),
        ):
            self.service.ask(image_doc.id, "这页有没有签字", self.db, user_id=self.user.id)

        self.assertEqual(captured["question"], "这页有没有签字")

    def test_document_ask_skips_visual_analysis_when_question_already_has_hint(self):
        image_doc = Document(
            user_id=self.user.id,
            title="scan.png",
            file_path="uploads/scan.png",
            file_type="png",
            status="indexed",
        )
        self.db.add(image_doc)
        self.db.commit()
        self.db.refresh(image_doc)

        question = "这页有没有签字\n\n补充视觉分析线索：图片下方疑似有签字区域。"
        captured = {}

        def fake_answer(question, document_id=None, user_id=None, **kwargs):
            captured["question"] = question
            return {
                "answer": "已使用现有视觉线索。",
                "citations": [],
                "confidence": 0.78,
                "can_answer": True,
                "hit_chunks": [],
                "latency_ms": 6,
            }

        with patch.object(
            self.service,
            "analyze_visual",
            new=AsyncMock(side_effect=AssertionError("should not call analyze_visual")),
        ), patch(
            "app.services.document_service.agentic_rag_service.answer",
            side_effect=fake_answer,
        ), patch(
            "app.services.document_service.document_qa_service.record",
            return_value=SimpleNamespace(
                id=1,
                feedback_value=None,
                feedback_status=None,
            ),
        ):
            result = self.service.ask(image_doc.id, question, self.db, user_id=self.user.id)

        self.assertEqual(captured["question"], question)
        self.assertTrue(result["can_answer"])


class DocumentAsyncParseDegradeTests(unittest.TestCase):
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
            username="doc_async_tester",
            email="doc_async_tester@example.com",
            hashed_password="secret",
        )
        self.db.add(self.user)
        self.db.commit()
        self.db.refresh(self.user)
        self.doc = Document(
            user_id=self.user.id,
            title="计划.md",
            file_path="uploads/plan.md",
            file_type="md",
            status="pending",
        )
        self.db.add(self.doc)
        self.db.commit()
        self.db.refresh(self.doc)

    def tearDown(self):
        self.db.close()

    def test_parse_document_task_degrades_when_indexing_fails(self):
        from app.tasks import parse_document_task
        fake_segments = [{"text": "content", "page_number": None, "section_title": "title", "section_path": ["title"], "segment_type": "paragraph"}]
        fake_self = SimpleNamespace(
            request=SimpleNamespace(id="task-1", retries=0),
            update_state=lambda *args, **kwargs: None,
        )

        with patch("app.tasks._extract_segments", return_value=fake_segments), patch(
            "app.tasks._split_text",
            return_value=[
                {
                    "chunk_index": 0,
                    "content": "content",
                    "page_number": None,
                    "section_title": "title",
                    "section_path": ["title", "扫描页"],
                    "segment_type": "page_ocr",
                    "table_like": False,
                }
            ],
        ), patch(
            "app.tasks._try_index_document",
            return_value=RuntimeError("embedding service unavailable"),
        ), patch(
            "app.tasks.log_async_task_event",
        ), patch(
            "app.tasks.document_job_service.mark_started",
        ), patch(
            "app.tasks.document_job_service.update_progress",
        ), patch(
            "app.tasks.document_job_service.mark_succeeded",
        ), patch(
            "app.tasks.SessionLocal",
            self.SessionLocal,
        ):
            result = parse_document_task.run.__func__(fake_self, self.doc.id, self.doc.file_path, self.doc.file_type)

        self.db.refresh(self.doc)
        stored_chunk = self.db.query(DocumentChunk).filter(DocumentChunk.document_id == self.doc.id).first()
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["indexed"])
        self.assertEqual(result["index_error"], "任务执行失败，请查看系统日志")
        self.assertEqual(self.doc.status, "parsed")
        self.assertEqual(stored_chunk.section_path, "title > 扫描页")
        self.assertEqual(stored_chunk.segment_type, "page_ocr")
        self.assertFalse(stored_chunk.table_like)

    def test_parse_document_task_fails_without_retry_for_permanent_ocr_error(self):
        from app.tasks import parse_document_task
        from app.services.document_service import DocumentParsePermanentError

        fake_self = SimpleNamespace(
            request=SimpleNamespace(id="task-ocr", retries=0),
            update_state=lambda *args, **kwargs: None,
            retry=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not retry")),
        )

        with patch(
            "app.tasks._extract_segments",
            side_effect=DocumentParsePermanentError("当前环境未启用 OCR，无法解析图片或扫描 PDF。"),
        ), patch(
            "app.tasks.log_async_task_event",
        ), patch(
            "app.tasks.document_job_service.mark_started",
        ), patch(
            "app.tasks.document_job_service.mark_failed",
        ) as mock_mark_failed, patch(
            "app.tasks.SessionLocal",
            self.SessionLocal,
        ):
            result = parse_document_task.run.__func__(fake_self, self.doc.id, self.doc.file_path, "png")

        self.db.refresh(self.doc)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["message"], "任务执行失败，请查看系统日志")
        self.assertEqual(self.doc.status, "failed")
        mock_mark_failed.assert_called_once()

    def test_parse_document_task_failure_log_and_job_error_are_redacted(self):
        from app.tasks import parse_document_task
        from app.models.document import DocumentParseJob

        fake_self = SimpleNamespace(
            request=SimpleNamespace(id="task-ocr-redacted", retries=2),
            update_state=lambda *args, **kwargs: None,
            retry=lambda *args, **kwargs: None,
        )
        self.db.add(
            DocumentParseJob(
                document_id=self.doc.id,
                user_id=self.user.id,
                job_type="parse",
                task_id="task-ocr-redacted",
                status="running",
            )
        )
        self.db.commit()

        logged_details = []

        def fake_log_async_task_event(**kwargs):
            logged_details.append(kwargs["detail"])

        with patch(
            "app.tasks._extract_segments",
            side_effect=RuntimeError("db_password=secret"),
        ), patch(
            "app.tasks.log_async_task_event",
            side_effect=fake_log_async_task_event,
        ), patch(
            "app.tasks.SessionLocal",
            self.SessionLocal,
        ):
            with self.assertRaises(RuntimeError):
                parse_document_task.run.__func__(fake_self, self.doc.id, self.doc.file_path, self.doc.file_type)

        job = self.db.query(DocumentParseJob).filter_by(task_id="task-ocr-redacted").one()
        self.assertTrue(any("error=redacted" in detail for detail in logged_details))
        self.assertFalse(any("secret" in detail for detail in logged_details))
        self.assertEqual(job.error_message, "任务执行失败，请查看系统日志")
