"""
routes/system.py — System endpoints: docs, health, A2A agent card.

    GET /docs                    — Scalar dark-mode API docs
    GET /health                  — Health check
    GET /.well-known/agent.json  — A2A Agent Card
"""

import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from a2a.agent_card import build_agent_card

router = APIRouter()

_BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

_DOCS_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Cash Collection API</title>
  <style>
    /* Prevent white flash before Scalar loads */
    *, *::before, *::after { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; background: #0d1117; color: #f0f6fc; }

    /* Inject brand colours into Scalar's CSS variable layer */
    :root {
      --scalar-color-accent:      #818cf8;   /* indigo-400               */
      --scalar-background-1:      #0d1117;   /* github-dark canvas        */
      --scalar-background-2:      #161b22;   /* raised surface            */
      --scalar-background-3:      #21262d;   /* deeper surface / sidebar  */
      --scalar-border-color:      #30363d;
      --scalar-color-1:           #f0f6fc;   /* primary text              */
      --scalar-color-2:           #c9d1d9;   /* secondary text            */
      --scalar-color-3:           #8b949e;   /* muted text                */
      --scalar-color-green:       #3fb950;
      --scalar-color-red:         #f85149;
      --scalar-color-yellow:      #d29922;
      --scalar-color-blue:        #58a6ff;
      --scalar-color-orange:      #e3b341;
      --scalar-color-purple:      #bc8cff;
    }
  </style>
</head>
<body>
  <script id="api-reference" data-url="/openapi.json"></script>
  <script>
    document.getElementById('api-reference').dataset.configuration = JSON.stringify({
      darkMode:   true,
      theme:      'saturn',
      layout:     'modern',
      defaultHttpClient: { targetKey: 'python', clientKey: 'requests' },
      hiddenClients: ['c', 'clojure', 'objc', 'ocaml', 'r', 'swift', 'go', 'java', 'kotlin', 'php', 'ruby'],
      metadata: {
        title: 'Cash Collection Email Drafter',
        description: 'AI-powered collection email drafting with CrewAI + MLflow evaluation.',
      },
      tagsSorter: 'alpha',
    });
  </script>
  <script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>
</body>
</html>"""


@router.get("/docs", include_in_schema=False)
def custom_docs():
    """Scalar-powered interactive API docs with dark mode."""
    return HTMLResponse(_DOCS_HTML)


@router.get("/health", tags=["System"])
def health():
    """Service health check."""
    return {"status": "ok", "service": "cash-collection-drafter", "version": "1.0.0"}


@router.get("/.well-known/agent.json", tags=["A2A"])
def agent_card():
    """
    A2A Agent Card — describes this agent's capabilities and skills.
    Served at the standard well-known path per the A2A specification.
    """
    return JSONResponse(build_agent_card(_BASE_URL))
