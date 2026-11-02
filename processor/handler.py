"""
SQS-triggered document processor (AWS Lambda).

For each queued message {"document_id": "<uuid>"}:
  UPLOADED -> PROCESSING -> PROCESSED   (simulated processing)
On error the message is retried by SQS (partial batch response). After the final
receive attempt the document is marked FAILED with a safe summary and the message
moves to the dead-letter queue. Document contents and credentials are never logged.
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import boto3
import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN", "")
DB_NAME = os.environ.get("DB_NAME", "dspdb")
MAX_RECEIVE_COUNT = int(os.environ.get("MAX_RECEIVE_COUNT", "3"))
PROCESSING_SECONDS = float(os.environ.get("PROCESSING_SECONDS", "2"))

_credentials = None
_connection = None


def _log(event: str, **fields) -> None:
    logger.info(json.dumps({"event": event, **fields}))


def get_credentials() -> dict:
    """Fetch DB credentials from Secrets Manager once per execution environment."""
    global _credentials
    if _credentials is None:
        client = boto3.client("secretsmanager")
        secret = json.loads(client.get_secret_value(SecretId=DB_SECRET_ARN)["SecretString"])
        _credentials = {
            "host": secret["host"],
            "port": int(secret.get("port", 5432)),
            "dbname": secret.get("dbname", DB_NAME),
            "user": secret["username"],
            "password": secret["password"],
        }
    return _credentials


def get_connection():
    """Return a live autocommit connection, reconnecting if the cached one was closed."""
    global _connection
    if _connection is None or _connection.closed:
        _connection = psycopg.connect(**get_credentials(), row_factory=dict_row, autocommit=True, connect_timeout=5)
    return _connection


def get_status(document_id: str):
    with get_connection().cursor() as cur:
        cur.execute("SELECT status FROM documents WHERE document_id = %s;", (document_id,))
        row = cur.fetchone()
        return row["status"] if row else None


def set_status(document_id: str, status: str, error_message=None) -> None:
    with get_connection().cursor() as cur:
        cur.execute(
            "UPDATE documents SET status = %s, error_message = %s, updated_at = %s WHERE document_id = %s;",
            (status, error_message, datetime.now(timezone.utc), document_id),
        )


def process_record(record: dict) -> None:
    """Process one SQS record. Raises on any failure so SQS retries it."""
    body = json.loads(record["body"])
    document_id = str(uuid.UUID(str(body["document_id"])))  # validates UUID format

    current = get_status(document_id)
    if current is None:
        _log("document_missing_skipped", document_id=document_id)
        return
    if current == "PROCESSED":
        _log("already_processed_skipped", document_id=document_id)
        return

    set_status(document_id, "PROCESSING")
    time.sleep(PROCESSING_SECONDS)  # simulate validation and extraction work
    set_status(document_id, "PROCESSED")
    _log("document_processed", document_id=document_id)


def lambda_handler(event, context):
    failures = []
    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            process_record(record)
        except Exception as exc:
            receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
            _log("record_failed", message_id=message_id, error=type(exc).__name__, receive_count=receive_count)
            if receive_count >= MAX_RECEIVE_COUNT:
                try:
                    document_id = str(uuid.UUID(str(json.loads(record["body"])["document_id"])))
                    set_status(document_id, "FAILED", "Document processing failed after multiple attempts.")
                except Exception as inner:
                    _log("mark_failed_error", message_id=message_id, error=type(inner).__name__)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
