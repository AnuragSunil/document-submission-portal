FROM python:3.12-slim

# Create non-root user for least privilege container execution
RUN useradd -m -u 1001 appuser

WORKDIR /app

# Install dependencies first for Docker layer caching
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source code and frontend assets (build context is repo root)
COPY --chown=appuser:appuser backend/ ./backend/
COPY --chown=appuser:appuser frontend/ ./frontend/

USER 1001

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
