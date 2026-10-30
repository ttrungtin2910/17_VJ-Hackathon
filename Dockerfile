FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && useradd --create-home --uid 10001 garblo
COPY alembic.ini ./
COPY migrations ./migrations
USER garblo
EXPOSE 8000
CMD ["uvicorn", "garblo.main:app", "--host", "0.0.0.0", "--port", "8000"]
