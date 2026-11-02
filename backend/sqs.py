import json
import logging

import boto3
from botocore.config import Config

from .config import AWS_REGION, QUEUE_URL

logger = logging.getLogger(__name__)


def get_sqs_client():
    """Create a boto3 SQS client with bounded timeouts so uploads never hang on the queue."""
    return boto3.client(
        "sqs",
        region_name=AWS_REGION,
        config=Config(connect_timeout=3, read_timeout=5, retries={"max_attempts": 2}),
    )


def enqueue_document(document_id: str) -> bool:
    """
    Send a processing message for a document to SQS.
    Returns True when queued, False when no queue is configured or the send failed
    (callers then fall back to in-process processing).
    """
    if not QUEUE_URL:
        return False
    try:
        get_sqs_client().send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps({"document_id": document_id}),
        )
        logger.info("Enqueued processing message: id=%s", document_id)
        return True
    except Exception as e:
        logger.error("Failed to enqueue processing message: id=%s error=%s", document_id, type(e).__name__)
        return False
