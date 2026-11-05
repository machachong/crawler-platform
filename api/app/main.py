import logging
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.auth import router as auth_router
from .api.tasks import router as task_router
from .api.publications import router as publication_router
from .api.workspace import router as workspace_router
from .config import CORS_ORIGINS, openai_api_key
from .database import database_ok, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="自助爬虫平台", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(task_router)
app.include_router(auth_router)
app.include_router(publication_router)
app.include_router(workspace_router)


@app.exception_handler(RequestValidationError)
async def validation_error(_request, exc):
    # FastAPI's default error includes input values, which can contain passwords.
    return JSONResponse(status_code=422, content={"detail": [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]})


@app.on_event("startup")
def _startup():
    init_db()
    logger.info("Database tables are ready")


@app.get("/api/v1/health")
def health_check():
    try:
        import scrapling

        scrapling_version = getattr(scrapling, "__version__", "unknown")
    except Exception:
        scrapling_version = None
    db_ok = database_ok()
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": db_ok,
        "scrapling": scrapling_version,
        "ai_configured": bool(openai_api_key()),
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.get("/")
def root():
    return {"service": "crawler-platform", "docs": "/docs", "health": "/api/v1/health"}
