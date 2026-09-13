# CloudBase Run image for the FastAPI API service.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Keep the runtime image to the API and migration assets. PostgreSQL support
# is installed here because the current CloudBase environment provides
# PostgreSQL rather than MySQL.
COPY pyproject.toml ./pyproject.toml
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
RUN pip install --upgrade pip \
    && pip install "." "psycopg[binary]>=3.2,<4"

EXPOSE 8080

# CloudBase Run supplies PORT when configured; 8080 is the local/default port.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]