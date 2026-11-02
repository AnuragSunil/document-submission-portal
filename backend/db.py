import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import psycopg
from psycopg.rows import dict_row

from .config import get_db_credentials

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS documents (
    document_id UUID PRIMARY KEY,
    original_filename VARCHAR(255) NOT NULL,
    s3_key VARCHAR(1024) NOT NULL,
    content_type VARCHAR(120) NOT NULL,
    size_bytes BIGINT NOT NULL,
    document_type VARCHAR(60) NOT NULL,
    description VARCHAR(500),
    status VARCHAR(20) NOT NULL,
    error_message VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def get_connection():
    """Create and return a new PostgreSQL connection with dictionary row factory."""
    creds = get_db_credentials()
    conn = psycopg.connect(
        host=creds["host"],
        port=creds["port"],
        dbname=creds["dbname"],
        user=creds["user"],
        password=creds["password"],
        row_factory=dict_row,
        autocommit=True,
    )
    return conn


def init_db() -> None:
    """Initialize the documents table on application startup."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(CREATE_TABLE_SQL)
        logger.info("Database schema initialized successfully.")
    except Exception as e:
        logger.warning("Database schema initialization deferred or failed: %s", type(e).__name__)


def check_db_health() -> bool:
    """Verify database connectivity."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                row = cur.fetchone()
                return row is not None
    except Exception:
        return False


def insert_document(doc: Dict[str, Any]) -> None:
    """Insert a new document metadata record."""
    sql = """
    INSERT INTO documents (
        document_id, original_filename, s3_key, content_type,
        size_bytes, document_type, description, status, error_message,
        created_at, updated_at
    ) VALUES (
        %(document_id)s, %(original_filename)s, %(s3_key)s, %(content_type)s,
        %(size_bytes)s, %(document_type)s, %(description)s, %(status)s, %(error_message)s,
        %(created_at)s, %(updated_at)s
    );
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, doc)


def get_document_by_id(document_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve document metadata by document_id UUID."""
    sql = "SELECT * FROM documents WHERE document_id = %s;"
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (document_id,))
            return cur.fetchone()


def list_documents(search: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Query documents with optional search and status filters, ordered newest first."""
    clauses = []
    params: List[Any] = []

    if status:
        clauses.append("status = %s")
        params.append(status)

    if search:
        clauses.append("(LOWER(original_filename) LIKE %s OR LOWER(description) LIKE %s)")
        search_pattern = f"%{search.lower()}%"
        params.extend([search_pattern, search_pattern])

    where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM documents {where_clause} ORDER BY created_at DESC;"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def update_document_status(document_id: str, status: str, error_message: Optional[str] = None) -> None:
    """Update processing status and timestamp for a document."""
    sql = """
    UPDATE documents
    SET status = %s, error_message = %s, updated_at = %s
    WHERE document_id = %s;
    """
    now = datetime.now(timezone.utc)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (status, error_message, now, document_id))


def get_document_stats() -> Dict[str, int]:
    """Calculate aggregated document counts by status."""
    sql = "SELECT status, COUNT(*) AS count FROM documents GROUP BY status;"
    stats = {
        "total": 0,
        "UPLOADED": 0,
        "PROCESSING": 0,
        "PROCESSED": 0,
        "FAILED": 0,
    }
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            for row in rows:
                st = row["status"]
                cnt = int(row["count"])
                if st in stats:
                    stats[st] = cnt
                stats["total"] += cnt
    return stats
