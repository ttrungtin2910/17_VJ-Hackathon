FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

FROM base AS bootstrap
CMD ["python", "-m", "garblo.bootstrap"]

FROM base AS api
EXPOSE 8000
CMD ["uvicorn", "garblo.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS worker
CMD ["garblo-worker"]

FROM base AS ml-worker
RUN pip install --no-cache-dir ".[ml]"
CMD ["garblo-ingestion-worker"]
