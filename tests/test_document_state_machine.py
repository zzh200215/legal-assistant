"""文档状态机：合法迁移 / 非法跳转拦截 / 历史别名 / 失败信息记录 / 阶段更新。"""

import unittest

from app.models.document import Document
from app.services.document_state import (
    DOCUMENT_STATUS_FAILED,
    DOCUMENT_STATUS_INDEXED,
    DOCUMENT_STATUS_INDEXING,
    DOCUMENT_STATUS_PARSED,
    DOCUMENT_STATUS_PARSING,
    DOCUMENT_STATUS_RETRYING,
    DOCUMENT_STATUS_UPLOADED,
    DocumentStateTransitionError,
    can_transition,
    normalize_status,
    transition_document,
    update_stage,
)


def _doc(status: str = DOCUMENT_STATUS_UPLOADED) -> Document:
    return Document(title="t", status=status, current_stage=status)


class DocumentStateMachineTests(unittest.TestCase):
    def test_allows_main_flow(self):
        doc = _doc()
        transition_document(doc, DOCUMENT_STATUS_PARSING, stage="parsing")
        transition_document(doc, DOCUMENT_STATUS_PARSED, stage="parsed")
        transition_document(doc, DOCUMENT_STATUS_INDEXING, stage="indexing")
        transition_document(doc, DOCUMENT_STATUS_INDEXED, stage="indexed")
        self.assertEqual(doc.status, DOCUMENT_STATUS_INDEXED)

    def test_rejects_illegal_jump(self):
        doc = _doc(DOCUMENT_STATUS_UPLOADED)
        with self.assertRaises(DocumentStateTransitionError):
            transition_document(doc, DOCUMENT_STATUS_INDEXED)

    def test_rejects_direct_index_from_uploaded(self):
        self.assertFalse(can_transition(DOCUMENT_STATUS_UPLOADED, DOCUMENT_STATUS_INDEXED))
        self.assertFalse(can_transition(DOCUMENT_STATUS_UPLOADED, DOCUMENT_STATUS_PARSED))

    def test_failure_and_retry_flow(self):
        doc = _doc(DOCUMENT_STATUS_PARSING)
        transition_document(
            doc,
            DOCUMENT_STATUS_FAILED,
            failure_stage="parsing",
            error_code="PARSE_FAILED",
            error_message="cannot parse",
        )
        self.assertEqual(doc.status, DOCUMENT_STATUS_FAILED)
        self.assertEqual(doc.failure_stage, "parsing")
        self.assertEqual(doc.error_code, "PARSE_FAILED")
        self.assertEqual(doc.error_message, "cannot parse")

        transition_document(doc, DOCUMENT_STATUS_RETRYING, stage="retrying")
        self.assertIsNone(doc.failure_stage)
        self.assertIsNone(doc.error_code)
        self.assertIsNone(doc.error_message)
        self.assertEqual(doc.status, DOCUMENT_STATUS_RETRYING)

        transition_document(doc, DOCUMENT_STATUS_PARSING, stage="parsing")
        self.assertEqual(doc.status, DOCUMENT_STATUS_PARSING)

    def test_index_degraded_to_parsed(self):
        doc = _doc(DOCUMENT_STATUS_INDEXING)
        transition_document(doc, DOCUMENT_STATUS_PARSED, failure_stage="indexing", error_code="INDEX_FAILED", error_message="boom")
        self.assertEqual(doc.status, DOCUMENT_STATUS_PARSED)
        self.assertEqual(doc.failure_stage, "indexing")

    def test_reindex_from_indexed(self):
        doc = _doc(DOCUMENT_STATUS_INDEXED)
        transition_document(doc, DOCUMENT_STATUS_PARSING, stage="parsing")
        self.assertEqual(doc.status, DOCUMENT_STATUS_PARSING)

    def test_retrying_to_parsed_allowed_for_chunk_resume(self):
        doc = _doc(DOCUMENT_STATUS_RETRYING)
        transition_document(doc, DOCUMENT_STATUS_PARSED, stage="parsed")
        self.assertEqual(doc.status, DOCUMENT_STATUS_PARSED)

    def test_legacy_pending_maps_to_uploaded(self):
        self.assertEqual(normalize_status("pending"), DOCUMENT_STATUS_UPLOADED)
        self.assertEqual(normalize_status("processing"), DOCUMENT_STATUS_PARSING)
        doc = _doc("pending")
        transition_document(doc, DOCUMENT_STATUS_PARSING, stage="parsing")
        self.assertEqual(doc.status, DOCUMENT_STATUS_PARSING)

    def test_update_stage_does_not_change_status(self):
        doc = _doc(DOCUMENT_STATUS_PARSED)
        update_stage(doc, "chunking")
        self.assertEqual(doc.status, DOCUMENT_STATUS_PARSED)
        self.assertEqual(doc.current_stage, "chunking")
        self.assertIsNotNone(doc.last_processed_at)

    def test_failed_records_failure_and_recovers(self):
        doc = _doc(DOCUMENT_STATUS_PARSED)
        transition_document(doc, DOCUMENT_STATUS_FAILED, failure_stage="chunking", error_code="CHUNK_FAILED", error_message="split error")
        self.assertEqual(doc.failure_stage, "chunking")
        transition_document(doc, DOCUMENT_STATUS_RETRYING)
        self.assertEqual(doc.status, DOCUMENT_STATUS_RETRYING)


if __name__ == "__main__":
    unittest.main()
