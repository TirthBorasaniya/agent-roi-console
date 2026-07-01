"""FastAPI application entry point for the Agent ROI Console."""

# ============= Standard Library =============
import logging
import os
from contextlib import asynccontextmanager

# ============= Third-Party =============
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ============= Local =============
from database import init_db
from routers import metrics, roi, runs, workflows

# ============= Constants =============
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
ALLOWED_ORIGINS = [FRONTEND_ORIGIN, "http://localhost:3000", "http://frontend:5173"]


# ============= Lifespan =============

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database and seed example workflows on startup."""
    logger.info("initializing database")
    init_db()
    logger.info("database ready")
    yield
    logger.info("shutting down")


# ============= Application =============

app = FastAPI(
    title="Agent ROI Console",
    description="Convert LangGraph workflow run outcomes into business-value estimates",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows.router)
app.include_router(runs.router)
app.include_router(metrics.router)
app.include_router(roi.router)


# ============= Health =============

@app.get("/health", tags=["health"])
def health_check() -> dict:
    """Return a simple health status for Docker healthchecks."""
    return {"status": "ok"}
