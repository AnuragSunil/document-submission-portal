import io
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import MAX_FILE_SIZE_BYTES, app

client = TestClient(app)

# In-memory document store for test isolation
_mock_db_store = {}


@pytest.fixture(autouse=True)
def reset_mock_db():
    """Reset the in-memory mock document database before each test."""
    _mock_db_store.clear()


@pytest.fixture(autouse=True)
def mock_db_operations(monkeypatch):
    """Mock database functions with in-memory implementations."""
    def mock_insert_document(doc):
        _mock_db_store[doc["document_id"]] = dict(doc)

    def mock_get_document_by_id(doc_id):
        return _mock_db_store.get(doc_id)

    def mock_list_documents(search=None, status=None):
        docs = list(_mock_db_store.values())
        if status:
            docs = [d for d in docs if d["status"] == status]
        if search:
            s = search.lower()
            docs = [
                d for d in docs
                if s in d["original_filename"].lower() or (d.get("description") and s in d["description"].lower())
            ]
        docs.sort(key=lambda d: d["created_at"], reverse=True)
        return docs

    def mock_update_document_status(doc_id, status, error_message=None):
        if doc_id in _mock_db_store:
            _mock_db_store[doc_id]["status"] = status
            _mock_db_store[doc_id]["error_message"] = error_message
            _mock_db_store[doc_id]["updated_at"] = datetime.now(timezone.utc)

    def mock_get_document_stats():
        stats = {"total": len(_mock_db_store), "UPLOADED": 0, "PROCESSING": 0, "PROCESSED": 0, "FAILED": 0}
        for d in _mock_db_store.values():
            st = d["status"]
            if st in stats:
                stats[st] += 1
        return stats

    monkeypatch.setattr("backend.main.insert_document", mock_insert_document)
    monkeypatch.setattr("backend.main.get_document_by_id", mock_get_document_by_id)
    monkeypatch.setattr("backend.main.list_documents", mock_list_documents)
    monkeypatch.setattr("backend.main.update_document_status", mock_update_document_status)
    monkeypatch.setattr("backend.main.get_document_stats", mock_get_document_stats)


@pytest.fixture(autouse=True)
def mock_s3_operations(monkeypatch):
    """Mock S3 upload, delete, and presigned URL operations."""
    mock_upload = MagicMock()
    mock_delete = MagicMock()
    mock_url = MagicMock(return_value="https://dsp-documents.s3.ap-south-1.amazonaws.com/test?AWSAccessKeyId=MOCK")

    monkeypatch.setattr("backend.main.upload_to_s3", mock_upload)
    monkeypatch.setattr("backend.main.delete_from_s3", mock_delete)
    monkeypatch.setattr("backend.main.generate_presigned_download_url", mock_url)

    return {
        "upload": mock_upload,
        "delete": mock_delete,
        "url": mock_url,
    }


def test_health_check():
    """Verify shallow /health endpoint returns 200 OK without dependencies."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_check_success(monkeypatch):
    """Verify /ready endpoint returns 200 when both DB and S3 are healthy."""
    monkeypatch.setattr("backend.main.check_db_health", lambda: True)
    monkeypatch.setattr("backend.main.check_s3_health", lambda: True)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_check_failure(monkeypatch):
    """Verify /ready endpoint returns 503 when dependencies are unreachable."""
    monkeypatch.setattr("backend.main.check_db_health", lambda: False)
    monkeypatch.setattr("backend.main.check_s3_health", lambda: True)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unready"


def test_upload_document_success():
    """Verify valid document upload creates record, uploads to S3, and returns 201."""
    file_content = b"%PDF-1.4 test document content"
    files = {"file": ("test_report.pdf", io.BytesIO(file_content), "application/pdf")}
    data = {"document_type": "Invoice", "description": "Test Invoice for Q1"}

    response = client.post("/api/documents", files=files, data=data)
    assert response.status_code == 201
    body = response.json()

    assert "document_id" in body
    assert body["original_filename"] == "test_report.pdf"
    assert body["document_type"] == "Invoice"
    assert body["status"] == "UPLOADED"
    assert body["size_bytes"] == len(file_content)


def test_upload_document_invalid_extension():
    """Verify upload fails with 400 when file extension is not in allowlist."""
    files = {"file": ("script.exe", io.BytesIO(b"executable content"), "application/octet-stream")}
    data = {"document_type": "Other"}

    response = client.post("/api/documents", files=files, data=data)
    assert response.status_code == 400
    assert "Unsupported file extension" in response.json()["detail"]


def test_upload_document_invalid_content_type():
    """Verify upload fails with 400 when content-type is unsupported."""
    files = {"file": ("valid.pdf", io.BytesIO(b"dummy"), "text/plain")}
    data = {"document_type": "Other"}

    response = client.post("/api/documents", files=files, data=data)
    assert response.status_code == 400
    assert "Unsupported content-type" in response.json()["detail"]


def test_upload_document_exceeds_size_limit():
    """Verify upload fails with 413 when file size exceeds 10 MB limit."""
    oversized_bytes = b"0" * (MAX_FILE_SIZE_BYTES + 1024)
    files = {"file": ("large_file.png", io.BytesIO(oversized_bytes), "image/png")}
    data = {"document_type": "Photo"}

    response = client.post("/api/documents", files=files, data=data)
    assert response.status_code == 413
    assert "exceeds maximum allowed size" in response.json()["detail"]


def test_upload_s3_cleanup_on_db_insert_failure(monkeypatch, mock_s3_operations):
    """Verify S3 object is rolled back/deleted if the database insert raises an error."""
    def fail_insert(_):
        raise RuntimeError("DB Connection Dropped")

    monkeypatch.setattr("backend.main.insert_document", fail_insert)

    files = {"file": ("test.docx", io.BytesIO(b"PK..."), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    data = {"document_type": "Contract"}

    response = client.post("/api/documents", files=files, data=data)
    assert response.status_code == 500
    assert mock_s3_operations["delete"].called


def test_get_documents_list_and_search():
    """Verify document list querying, search filter, and status filter."""
    doc_id_1 = str(uuid.uuid4())
    doc_id_2 = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    _mock_db_store[doc_id_1] = {
        "document_id": doc_id_1,
        "original_filename": "tax_2026.pdf",
        "s3_key": f"documents/{doc_id_1}.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        "document_type": "Tax",
        "description": "Annual report",
        "status": "PROCESSED",
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }

    _mock_db_store[doc_id_2] = {
        "document_id": doc_id_2,
        "original_filename": "id_card.png",
        "s3_key": f"documents/{doc_id_2}.png",
        "content_type": "image/png",
        "size_bytes": 2048,
        "document_type": "ID",
        "description": "Identity card scan",
        "status": "FAILED",
        "error_message": "Corrupted image file",
        "created_at": now,
        "updated_at": now,
    }

    # All documents
    res_all = client.get("/api/documents")
    assert res_all.status_code == 200
    assert len(res_all.json()) == 2

    # Status filter
    res_processed = client.get("/api/documents?status=PROCESSED")
    assert res_processed.status_code == 200
    assert len(res_processed.json()) == 1
    assert res_processed.json()[0]["document_id"] == doc_id_1

    # Search filter
    res_search = client.get("/api/documents?search=tax")
    assert res_search.status_code == 200
    assert len(res_search.json()) == 1
    assert res_search.json()[0]["original_filename"] == "tax_2026.pdf"


def test_get_document_by_id():
    """Verify single document retrieval and error cases (404 and 422)."""
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    _mock_db_store[doc_id] = {
        "document_id": doc_id,
        "original_filename": "passport.jpg",
        "s3_key": f"documents/{doc_id}.jpg",
        "content_type": "image/jpeg",
        "size_bytes": 5000,
        "document_type": "Passport",
        "description": None,
        "status": "UPLOADED",
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }

    res_found = client.get(f"/api/documents/{doc_id}")
    assert res_found.status_code == 200
    assert res_found.json()["document_id"] == doc_id

    res_not_found = client.get(f"/api/documents/{uuid.uuid4()}")
    assert res_not_found.status_code == 404

    res_invalid_uuid = client.get("/api/documents/invalid-uuid-string")
    assert res_invalid_uuid.status_code == 422


def test_download_presigned_url():
    """Verify presigned download URL generation for valid document."""
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    _mock_db_store[doc_id] = {
        "document_id": doc_id,
        "original_filename": "myfile.pdf",
        "s3_key": f"documents/{doc_id}.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        "document_type": "Legal",
        "description": None,
        "status": "PROCESSED",
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }

    response = client.get(f"/api/documents/{doc_id}/download")
    assert response.status_code == 200
    assert "url" in response.json()
    assert response.json()["url"].startswith("https://")


def test_get_stats():
    """Verify stats endpoint aggregates counts by status."""
    now = datetime.now(timezone.utc)
    for i, st in enumerate(["UPLOADED", "PROCESSING", "PROCESSED", "PROCESSED", "FAILED"]):
        uid = str(uuid.uuid4())
        _mock_db_store[uid] = {
            "document_id": uid,
            "original_filename": f"file_{i}.pdf",
            "s3_key": f"documents/{uid}.pdf",
            "content_type": "application/pdf",
            "size_bytes": 100,
            "document_type": "General",
            "description": None,
            "status": st,
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }

    response = client.get("/api/stats")
    assert response.status_code == 200
    stats = response.json()
    assert stats["total"] == 5
    assert stats["UPLOADED"] == 1
    assert stats["PROCESSING"] == 1
    assert stats["PROCESSED"] == 2
    assert stats["FAILED"] == 1


def test_retry_document():
    """Verify retry is allowed only for FAILED documents and transitions status to UPLOADED."""
    doc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    _mock_db_store[doc_id] = {
        "document_id": doc_id,
        "original_filename": "failed_doc.pdf",
        "s3_key": f"documents/{doc_id}.pdf",
        "content_type": "application/pdf",
        "size_bytes": 500,
        "document_type": "Invoice",
        "description": None,
        "status": "FAILED",
        "error_message": "Network timeout",
        "created_at": now,
        "updated_at": now,
    }

    # Retry should succeed
    response = client.post(f"/api/documents/{doc_id}/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "UPLOADED"

    # Retry on non-failed should return 400
    _mock_db_store[doc_id]["status"] = "PROCESSED"
    bad_retry = client.post(f"/api/documents/{doc_id}/retry")
    assert bad_retry.status_code == 400
    assert "Only documents in FAILED status" in bad_retry.json()["detail"]
