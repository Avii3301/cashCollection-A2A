FROM python:3.11-slim

WORKDIR /app

# ── Dependency layer (cached until pyproject.toml changes) ──────────────────
# Strategy: create stub package directories so `pip install .` can resolve ALL
# transitive deps (including crewai_tools ↔ mcp version pinning) in one pass,
# then replace stubs with real source without reinstalling deps.
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    mkdir -p crew a2a evaluation && \
    touch crew/__init__.py a2a/__init__.py evaluation/__init__.py && \
    pip install --no-cache-dir . && \
    rm -rf crew a2a evaluation

# ── Application layer ───────────────────────────────────────────────────────
COPY . .
RUN pip install --no-cache-dir --no-deps .

# ── Runtime config ──────────────────────────────────────────────────────────
# PYTHONPATH lets the MCP subprocess import top-level modules (crm.py)
ENV PYTHONPATH=/app
# Suppress GitPython warning when git is not in the container
ENV GIT_PYTHON_REFRESH=quiet

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
