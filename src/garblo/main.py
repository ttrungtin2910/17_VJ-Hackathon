from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from garblo.api.routes import router
from garblo.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["system"])
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "environment": settings.app_env,
        "ai_configured": settings.ai_is_configured,
        "storage_configured": settings.storage_is_configured,
    }


@app.get("/mock", include_in_schema=False)
def mock_ui() -> FileResponse:
    if not settings.enable_api_mock_ui:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="API mock UI is disabled")
    return FileResponse(Path(__file__).parent / "static" / "mock.html", media_type="text/html")


def run() -> None:
    uvicorn.run(
        "garblo.main:app", host="0.0.0.0", port=8000, reload=settings.app_env == "development"
    )
