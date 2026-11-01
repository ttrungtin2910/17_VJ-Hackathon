FROM python:3.12.14-slim-bookworm@sha256:0f5b26b9518d002b6173fd61daad821fa340635ebfec5bba471013f9ca114579

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
