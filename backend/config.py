import json
import logging
import os
from typing import Any, Dict, Optional
import boto3

logger = logging.getLogger(__name__)

AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")
BUCKET_NAME = os.getenv("BUCKET_NAME", "")
DB_SECRET_ARN = os.getenv("DB_SECRET_ARN", "")
DB_NAME = os.getenv("DB_NAME", "dspdb")

_cached_db_credentials: Optional[Dict[str, Any]] = None


def get_db_credentials() -> Dict[str, Any]:
    """
    Retrieve database connection parameters.
    If DB_SECRET_ARN is provided, fetches credentials from AWS Secrets Manager and caches them.
    Falls back to individual environment variables for local testing.
    """
    global _cached_db_credentials
    if _cached_db_credentials is not None:
        return _cached_db_credentials

    db_params = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "dbname": DB_NAME,
        "user": os.getenv("DB_USER", "dspadmin"),
        "password": os.getenv("DB_PASSWORD", ""),
    }

    if DB_SECRET_ARN:
        try:
            client = boto3.client("secretsmanager", region_name=AWS_REGION)
            response = client.get_secret_value(SecretId=DB_SECRET_ARN)
            secret_str = response.get("SecretString")
            if secret_str:
                secret_dict = json.loads(secret_str)
                if "host" in secret_dict:
                    db_params["host"] = secret_dict["host"]
                if "port" in secret_dict:
                    db_params["port"] = int(secret_dict["port"])
                if "username" in secret_dict:
                    db_params["user"] = secret_dict["username"]
                if "password" in secret_dict:
                    db_params["password"] = secret_dict["password"]
                if "dbname" in secret_dict:
                    db_params["dbname"] = secret_dict["dbname"]
            logger.info("Database credentials loaded from Secrets Manager.")
        except Exception as e:
            logger.error("Failed to load secret from Secrets Manager: %s", type(e).__name__)
            raise

    _cached_db_credentials = db_params
    return _cached_db_credentials
