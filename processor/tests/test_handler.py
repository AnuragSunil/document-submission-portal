import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import handler  # noqa: E402


@pytest.fixture(autouse=True)
def fake_db(monkeypatch):
    """In-memory status store replacing the PostgreSQL helpers."""
    store = {}
    monkeypatch.setattr(handler, "PROCESSING_SECONDS", 0)
    monkeypatch.setattr(handler, "get_status", lambda doc_id: store.get(doc_id))
    monkeypatch.setattr(
        handler, "set_status", lambda doc_id, status, error_message=None: store.__setitem__(doc_id, status)
    )
    return store


def make_record(document_id, message_id="m1", receive_count=1):
    return {
        "messageId": message_id,
        "body": json.dumps({"document_id": document_id}),
        "attributes": {"ApproximateReceiveCount": str(receive_count)},
    }


def test_processes_uploaded_document(fake_db):
    doc_id = str(uuid.uuid4())
    fake_db[doc_id] = "UPLOADED"
    result = handler.lambda_handler({"Records": [make_record(doc_id)]}, None)
    assert result == {"batchItemFailures": []}
    assert fake_db[doc_id] == "PROCESSED"


def test_missing_document_is_skipped_without_failure(fake_db):
    result = handler.lambda_handler({"Records": [make_record(str(uuid.uuid4()))]}, None)
    assert result == {"batchItemFailures": []}


def test_already_processed_is_idempotent(fake_db):
    doc_id = str(uuid.uuid4())
    fake_db[doc_id] = "PROCESSED"
    result = handler.lambda_handler({"Records": [make_record(doc_id)]}, None)
    assert result == {"batchItemFailures": []}
    assert fake_db[doc_id] == "PROCESSED"


def test_failure_is_reported_for_retry_before_final_attempt(fake_db, monkeypatch):
    doc_id = str(uuid.uuid4())
    fake_db[doc_id] = "UPLOADED"

    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(handler, "get_status", boom)
    result = handler.lambda_handler({"Records": [make_record(doc_id, "m9", receive_count=1)]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "m9"}]}
    assert fake_db[doc_id] == "UPLOADED"


def test_final_attempt_marks_document_failed(fake_db, monkeypatch):
    doc_id = str(uuid.uuid4())
    fake_db[doc_id] = "UPLOADED"
    calls = {"n": 0}

    def flaky_get(_doc_id):
        calls["n"] += 1
        raise RuntimeError("db down")

    monkeypatch.setattr(handler, "get_status", flaky_get)
    result = handler.lambda_handler({"Records": [make_record(doc_id, "m3", receive_count=3)]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "m3"}]}
    assert fake_db[doc_id] == "FAILED"


def test_invalid_uuid_is_rejected_and_retried(fake_db):
    result = handler.lambda_handler({"Records": [make_record("not-a-uuid", "bad")]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "bad"}]}
