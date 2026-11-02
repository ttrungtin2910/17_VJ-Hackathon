# Garblo Backend

FastAPI backend for a private AI wardrobe. MongoDB is the application source of truth, RustFS stores private images, and Qdrant stores isolated per-user garment vectors.

## Local start

1. Copy `.env.example` to `.env`, set non-default local secrets, then set `FPT_API_KEY` and `OPEN_AI_API_KEY`.
2. Start API dependencies and API:

~~~powershell
docker compose up --build
~~~

Services: API/docs `http://localhost:8000/docs`, mock UI `http://localhost:8000/mock`, RustFS console `http://localhost:9001`, Qdrant `http://localhost:6333/dashboard`.

Start the independently debuggable AI workers only when their dependencies and credentials are ready:

~~~powershell
# Local Grounding DINO + SigLIP2 ingestion worker; safe CPU fallback.
docker compose --profile ml up --build ingestion-worker

# NVIDIA-enabled variant for the RTX 3060 (requires Docker GPU support).
docker compose --profile ml-gpu up --build ingestion-worker-gpu

# FPT recommendation worker and OpenAI synthetic-render worker.
docker compose --profile ai up --build recommendation-worker render-worker
~~~

For direct local debugging, install only what the selected process needs:

~~~powershell
pip install -e '.[dev]'
garblo-api

# Requires the optional local-model dependencies.
pip install -e '.[dev,ml]'
garblo-ingestion-worker
garblo-recommendation-worker
garblo-render-worker
~~~

## AI configuration

- FPT intent parsing uses `POST /v1/messages`, `FPT_MODEL=Azure:gpt-5.6-terra`, and only consumes response blocks with `type: "text"`. Internal `thinking` blocks are never stored or exposed.
- OpenAI vision metadata defaults to `gpt-4.1-mini`; embeddings use `text-embedding-3-large` at 3,072 dimensions; synthetic outfit image edits use `gpt-image-2`.
- Ingestion uses local Grounding DINO bounding boxes and local SigLIP2 image embeddings. Model weights download only when the ingestion worker starts.
- The renderer produces a synthetic adult model; it is not virtual try-on of the user.

`python scripts/check_fpt_token_api.py` is a safe FPT smoke test. It reads `.env`, prints only public text output, and redacts the API key from errors. It will report HTTP 402 if the account has no usable credit.

## Main workflow APIs

- `POST /v1/uploads` with repeated `files` form fields stores up to 20 images and creates one ingestion job per source image.
- `GET /v1/uploads/{upload_id}` shows batch/job progress.
- `POST /v1/wardrobe-searches` queues FPT intent parsing plus Qdrant retrieval; `GET /v1/wardrobe-searches/{search_id}` returns normalized intent and recommendations.
- Existing `POST /v1/lookbooks` and `POST /v1/looks/{look_id}/renders` enqueue synthetic outfit renders.
- All AI operations require an active `ai_processing` consent and enforce user ownership.

### Upload images with curl (PowerShell)

Log in through `POST /v1/auth/login`, create an `ai_processing` consent through `POST /v1/consents`, then replace `YOUR_ACCESS_TOKEN` below with the returned `access_token`.

~~~powershell
curl.exe -X POST "http://localhost:8000/v1/uploads" `
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" `
  -F "files=@C:\Users\admin\Downloads\shirt.jpg;type=image/jpeg" `
  -F "files=@C:\Users\admin\Downloads\trousers.png;type=image/png" `
  -F "asset_type=garment_photo"
~~~

The request must be `multipart/form-data`; do not send a local path or Base64 image text as JSON. The response contains an `upload_id` and one ingestion ID in `job_ids` per uploaded source image.

## Tests

~~~powershell
pytest -q
ruff check src tests scripts
docker compose config --quiet
~~~

Default tests use `mongomock` and fakes: they neither download ML weights nor make paid FPT/OpenAI requests. Live provider smoke tests are intentionally opt-in through `RUN_LIVE_AI_TESTS=1` after keys and billing are verified.
