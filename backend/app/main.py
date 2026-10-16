import sys
from pathlib import Path

if __package__ in (None, ""):  # `python app/main.py`: sem isto, o Python não encontra o pacote `app`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import importer, insights, quant, sync
from app.core.config import get_settings
from app.database import dispose_engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sync.router, prefix="/api/v1")
app.include_router(quant.router, prefix="/api/v1")
app.include_router(insights.router, prefix="/api/v1")
app.include_router(importer.router, prefix="/api/v1")


@app.get("/health", tags=["infra"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


def run() -> None:
    """Sobe a API em http://127.0.0.1:8000 (docs em /docs). Equivale a `uvicorn app.main:app --reload`."""
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    run()
