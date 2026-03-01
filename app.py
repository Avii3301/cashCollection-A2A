"""
app.py — Application entry point.

Creates the FastAPI instance, configures MLflow on startup, and wires
the three route modules together.

    routes/system.py  →  GET  /docs, /health, /.well-known/agent.json
    routes/a2a.py     →  POST /a2a
    routes/draft.py   →  POST /draft

Run:
    uvicorn app:app --reload --port 8000
"""

import logging
import os
from contextlib import asynccontextmanager

import mlflow
from dotenv import load_dotenv
from fastapi import FastAPI

from routes.a2a import router as a2a_router
from routes.draft import router as draft_router
from routes.system import router as system_router

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

_BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# MLflow setup
# ---------------------------------------------------------------------------
def _setup_mlflow() -> None:
    """Configure MLflow tracking URI, experiment, and CrewAI autolog."""
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "databricks")
    mlflow.set_tracking_uri(tracking_uri)

    experiment_name = os.environ.get("MLFLOW_EXPERIMENT_NAME", "cash-collection-drafter")
    try:
        mlflow.set_experiment(experiment_name)
        logger.info("MLflow experiment: %s", experiment_name)
    except Exception as exc:
        logger.warning(
            "Could not set MLflow experiment '%s': %s. "
            "Tracking will use the default experiment.",
            experiment_name,
            exc,
        )

    try:
        mlflow.crewai.autolog()
        logger.info("MLflow CrewAI autolog enabled")
    except Exception as exc:
        logger.warning("MLflow crewai autolog not available: %s", exc)

    logger.info("MLflow tracking URI: %s", tracking_uri)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_mlflow()
    print(f"\n  ✓ Service ready at: {_BASE_URL}\n  ✓ Interactive docs: {_BASE_URL}/docs\n", flush=True)
    yield
    logger.info("Cash Collection Email Drafter shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Cash Collection Email Drafter",
    description=(
        "CrewAI-powered service that drafts collection emails for invoice batches. "
        "Fetches CRM data via MCP, decides tone (0-5), and produces subject + body."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,   # replaced by Scalar UI in routes/system.py
    redoc_url=None,
)

app.include_router(system_router)
app.include_router(a2a_router)
app.include_router(draft_router)
