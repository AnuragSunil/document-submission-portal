FROM registry.redhat.io/ubi9/python-311-minimal:latest

# Create non-root user
RUN useradd -m -u 1001 appuser

WORKDIR /app

# Copy dependencies and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY --chown=appuser:appuser . .

USER 1001

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000"]
