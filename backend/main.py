"""FastAPI application entry point for the Agent ROI Console."""

# ============= Standard Library =============
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# ============= Third-Party =============
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ============= Local =============
from database import init_db
from routers import metrics, roi, runs, workflows

# ============= Constants =============
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
ALLOWED_ORIGINS = [FRONTEND_ORIGIN, "http://localhost:3000", "http://frontend:5173"]
ALLOW_CREDENTIALS = True
WILDCARD_ORIGIN = "*"

# built frontend bundle, copied here by the deployment image. absent during
# local two-service development, where Vite serves the app instead
STATIC_DIR = Path(__file__).parent / "static"


# ============= Configuration Guards =============

def assert_cors_config_safe(
    allowed_origins: list[str],
    o_allow_credentials: bool,
) -> None:
    """
    Reject a wildcard CORS origin that is paired with credentialed requests.

    Sending credentials to a wildcard origin is rejected by every browser, so
    this combination is never usable in practice; it only appears when someone
    loosens the origin list to debug a CORS error. Failing at startup makes
    that mistake obvious instead of surfacing as an opaque browser error.

    Parameters
    ----------
    allowed_origins : list[str]
        Origins that will be handed to CORSMiddleware.
    o_allow_credentials : bool
        Whether CORSMiddleware is configured to allow credentialed requests.

    Raises
    ------
    RuntimeError
        If any origin is the wildcard while credentials are allowed.
    """
    if o_allow_credentials and WILDCARD_ORIGIN in allowed_origins:
        raise RuntimeError(
            "refusing to start: FRONTEND_ORIGIN is '*' while CORS credentials "
            "are enabled. Set FRONTEND_ORIGIN to an explicit origin such as "
            "http://localhost:5173, or disable ALLOW_CREDENTIALS."
        )


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

# fail fast at import time, before the server binds a port
assert_cors_config_safe(ALLOWED_ORIGINS, ALLOW_CREDENTIALS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
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


# ============= Static Frontend =============

# mounted last so the API routers, /docs and /health above take precedence.
# html=True serves index.html at "/", collapsing the stack to one service and
# one origin; skipped when the bundle is absent so local dev still works
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="frontend")
    logger.info(f"serving frontend bundle from {STATIC_DIR}")
else:
    logger.info("no frontend bundle found, running API-only (use Vite for the UI)")
