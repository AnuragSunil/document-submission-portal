import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .db import (
    check_db_health,
    get_document_by_id,
    get_document_stats,
    init_db,
    insert_document,
    list_documents,
    update_document_status,
)
from .s3 import check_s3_health, delete_from_s3, generate_presigned_download_url, upload_to_s3

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("dsp-backend")

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB limit
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg"}
ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png",
    "image/jpeg",
    "image/pjpeg",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown hooks."""
    logger.info("Initializing Document Submission Portal backend...")
    init_db()
    yield
    logger.info("Shutting down backend...")


app = FastAPI(
    title="Document Submission Portal API",
    description="REST API for secure document upload, metadata storage, and status processing.",
    version="1.0.0",
    lifespan=lifespan,
)


class DocumentResponse(BaseModel):
    document_id: uuid.UUID
    original_filename: str
    content_type: str
    size_bytes: int
    document_type: str
    description: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class StatsResponse(BaseModel):
    total: int
    UPLOADED: int
    PROCESSING: int
    PROCESSED: int
    FAILED: int


class PresignedUrlResponse(BaseModel):
    url: str


async def process_document_background(document_id: str) -> None:
    """Simulate document processing workflow: UPLOADED -> PROCESSING -> PROCESSED (or FAILED)."""
    try:
        logger.info("Processing document start: id=%s", document_id)
        update_document_status(document_id, "PROCESSING")
        await asyncio.sleep(2)  # Simulate validation & extraction work
        update_document_status(document_id, "PROCESSED")
        logger.info("Processing document completed: id=%s", document_id)
    except Exception as e:
        logger.error("Processing document failed: id=%s error=%s", document_id, type(e).__name__)
        update_document_status(document_id, "FAILED", error_message="Document processing failed during extraction.")


# =========================================================================
# Health & Readiness Endpoints
# =========================================================================
@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """Unconditional shallow health check for ALB target group."""
    return {"status": "ok"}


@app.get("/ready", status_code=status.HTTP_200_OK)
def readiness_check():
    """Deep readiness probe checking RDS database and S3 reachability."""
    db_ok = check_db_health()
    s3_ok = check_s3_health()

    if not db_ok or not s3_ok:
        errors = []
        if not db_ok:
            errors.append("Database unreachable")
        if not s3_ok:
            errors.append("S3 bucket unreachable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unready", "errors": errors},
        )
    return {"status": "ready"}


# =========================================================================
# Document Submission & Query Endpoints
# =========================================================================
@app.post("/api/documents", status_code=status.HTTP_201_CREATED, response_model=DocumentResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    description: Optional[str] = Form(None),
):
    """
    Validate, store, and initiate processing for an uploaded document.
    Enforces strict MIME/extension allowlists and 10MB chunked size streaming limit.
    """
    raw_filename = file.filename or "unknown"
    _, ext = os.path.splitext(raw_filename)
    ext_lower = ext.lower()

    if ext_lower not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file extension '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    content_type = file.content_type or "application/octet-stream"
    if content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported content-type '{content_type}'. Allowed: PDF, DOCX, PNG, JPG.",
        )

    # Read chunk-by-chunk to enforce max 10MB memory/payload boundary
    chunks = []
    total_bytes = 0
    chunk_size = 64 * 1024  # 64 KB chunks

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_bytes += len(chunk)
        if total_bytes > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB.",
            )
        chunks.append(chunk)

    file_bytes = b"".join(chunks)
    document_id = str(uuid.uuid4())
    s3_key = f"documents/{document_id}{ext_lower}"
    now = datetime.now(timezone.utc)

    # 1. Upload to S3
    try:
        upload_to_s3(file_bytes, s3_key, content_type)
        logger.info("Uploaded to S3: id=%s size=%d", document_id, total_bytes)
    except Exception as e:
        logger.error("S3 upload failed: %s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist document to storage.",
        )

    # 2. Insert metadata into RDS PostgreSQL
    doc_record = {
        "document_id": document_id,
        "original_filename": raw_filename[:255],
        "s3_key": s3_key,
        "content_type": content_type,
        "size_bytes": total_bytes,
        "document_type": document_type[:60],
        "description": description[:500] if description else None,
        "status": "UPLOADED",
        "error_message": None,
        "created_at": now,
        "updated_at": now,
    }

    try:
        insert_document(doc_record)
        logger.info("Inserted document record: id=%s", document_id)
    except Exception as e:
        logger.error("DB insert failed, rolling back S3 object: %s", type(e).__name__)
        delete_from_s3(s3_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to record document metadata.",
        )

    # 3. Launch background processing simulation
    background_tasks.add_task(process_document_background, document_id)

    return doc_record


@app.get("/api/documents", response_model=List[DocumentResponse])
def get_documents(
    search: Optional[str] = Query(None, description="Search term for filename or description"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
):
    """Retrieve documents ordered newest first with optional search and status filters."""
    return list_documents(search=search, status=status_filter)


@app.get("/api/documents/{document_id}", response_model=DocumentResponse)
def get_document(document_id: str):
    """Retrieve single document metadata by UUID."""
    try:
        uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid document UUID format.")

    doc = get_document_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return doc


@app.get("/api/documents/{document_id}/download", response_model=PresignedUrlResponse)
def download_document(document_id: str):
    """Generate a short-lived (60-second) presigned download URL for a document."""
    try:
        uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid document UUID format.")

    doc = get_document_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    try:
        url = generate_presigned_download_url(
            s3_key=doc["s3_key"],
            original_filename=doc["original_filename"],
            expires_in=60,
        )
        return {"url": url}
    except Exception as e:
        logger.error("Failed generating download URL: %s", type(e).__name__)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed generating download link.")


@app.get("/api/stats", response_model=StatsResponse)
def get_stats():
    """Retrieve aggregate document statistics by status."""
    return get_document_stats()


@app.post("/api/documents/{document_id}/retry", response_model=DocumentResponse)
def retry_document(document_id: str, background_tasks: BackgroundTasks):
    """Retry processing for a document currently in FAILED state."""
    try:
        uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid document UUID format.")

    doc = get_document_by_id(document_id)
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    if doc["status"] != "FAILED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only documents in FAILED status can be retried (current status: {doc['status']}).",
        )

    update_document_status(document_id, "UPLOADED", error_message=None)
    background_tasks.add_task(process_document_background, document_id)

    updated_doc = get_document_by_id(document_id)
    return updated_doc


# =========================================================================
# Static Frontend Mount (Mounted last so /api and /health are never shadowed)
# =========================================================================
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
if os.path.isdir(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
