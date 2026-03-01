# Cash Collection Email Drafter

A production-ready **multi-agent AI service** that automates accounts-receivable outreach. Given a batch of overdue invoices, it fetches live CRM data, decides the right communication tone per client relationship, drafts a tailored collection email, evaluates it through a quality pipeline, and logs every metric to MLflow — all in a single API call.

---

## Why This Exists

Writing collection emails is tedious, error-prone, and relationship-sensitive. Blasting every overdue client with the same firm reminder alienates high-value partners; being too polite with serial defaulters sends the wrong signal. This service solves that by:

- Reading client history from a CRM tool
- Running it through a structured **tone rubric (0 = firm → 5 = polite)**
- Generating a contextually appropriate email
- Automatically scoring it for quality before it ever reaches a human

---

## Tech Stack

| Layer | Technology |
|---|---|
| Agent Framework | [CrewAI](https://github.com/crewAIInc/crewAI) |
| LLM | OpenAI `gpt-4o-mini` |
| Tool Protocol | [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) — STDIO transport |
| Agent Interop | [Google A2A Protocol](https://github.com/google-a2a/A2A) — JSON-RPC 2.0 |
| API | FastAPI + Uvicorn |
| Evaluation | MLflow custom scorers + optional LLM-as-judge |
| Experiment Tracking | MLflow → Databricks |
| Containerisation | Docker + Docker Compose |
| Language | Python 3.11+ |

---

## Architecture at a Glance

```
                        ┌─────────────────────────────────┐
  Caller / A2A Agent ──►│         FastAPI  :8000          │
                        │  /draft   /a2a   /agent.json    │
                        └──────────────┬──────────────────┘
                                       │
                        ┌──────────────▼──────────────────┐
                        │        CrewAI  Crew              │
                        │  ┌──────────┐  ┌─────────────┐  │
                        │  │  Agent 1 │  │   Agent 2   │  │
                        │  │   CRM    │  │    Tone     │  │
                        │  │ Fetcher  │  │  Analyzer   │  │
                        │  └────┬─────┘  └──────┬──────┘  │
                        │       │ MCP            │         │
                        │  ┌────▼─────┐  ┌──────▼──────┐  │
                        │  │   MCP    │  │   Agent 3   │  │
                        │  │  Server  │  │    Email    │  │
                        │  │ (STDIO)  │  │   Drafter   │  │
                        │  └────┬─────┘  └──────┬──────┘  │
                        │       │               │          │
                        └───────┼───────────────┼──────────┘
                                │               │
                          ┌─────▼────┐   ┌──────▼──────────┐
                          │  CRM DB  │   │    Scorers +    │
                          │ (mock)   │   │  MLflow / DBKS  │
                          └──────────┘   └─────────────────┘
```

> See [DESIGN.md](DESIGN.md) for the full design document with detailed data-flow diagrams and protocol walkthroughs.

---

## Quick Start

**Prerequisites:** Docker, Docker Compose, an OpenAI API key, and optionally a Databricks workspace.

### 1. Clone and configure

```bash
git clone https://github.com/Avii3301/cashCollection-A2A.git
cd cashCollection-A2A
cp .env.example .env
# Fill in your keys in .env
```

### 2. Start with Docker

```bash
docker compose up --build
```

The service starts on `http://localhost:8000`. The first build takes a few minutes to install all dependencies.

### 3. Health check

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"cash-collection-drafter","version":"1.0.0"}
```

---

## Local Development (without Docker)

**Prerequisites:** Python 3.11+

```bash
# 1. Clone
git clone https://github.com/Avii3301/cashCollection-A2A.git
cd cashCollection-A2A

# 2. Create and activate a virtual environment
python3.11 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -e .

# 4. Configure environment
cp .env.example .env
# Edit .env — fill in OPENAI_API_KEY and Databricks credentials
# To skip Databricks entirely, set:
#   MLFLOW_TRACKING_URI=http://127.0.0.1:5000
# and run:  mlflow server --port 5000

# 5. Run
uvicorn app:app --reload --port 8000
```

The service is now live at `http://localhost:8000`.

> **Note:** The MCP server (`mcp_server.py`) is loaded in-process by the crew — no subprocess or separate process needed.

---

## Interactive API Docs

Once the service is running, open **[http://localhost:8000/docs](http://localhost:8000/docs)** in your browser.

You get a full Swagger UI with pre-filled examples — hit **Try it out** on any endpoint and click **Execute**. No Postman or curl needed.

| UI | URL |
|---|---|
| Swagger UI | `http://localhost:8000/docs` |
| ReDoc | `http://localhost:8000/redoc` |

---

## API Reference

### `POST /draft` — Direct batch processing

```bash
curl -X POST http://localhost:8000/draft \
  -H "Content-Type: application/json" \
  -d '{
    "invoices": [
      {
        "invoice_number": "INV-001",
        "company_name": "Blackstone Retail Ltd",
        "amount": 47500.00,
        "due_date": "2025-07-01"
      },
      {
        "invoice_number": "INV-006",
        "company_name": "Sterling Global Partners",
        "amount": 125000.00,
        "due_date": "2025-09-15"
      }
    ]
  }'
```

**Response**

```json
{
  "results": [
    {
      "invoice_number": "INV-001",
      "tone_score": 0,
      "subject": "Final Notice: Invoice INV-001 — Immediate Payment Required",
      "description": "Dear Blackstone Retail Ltd Finance Team, ..."
    },
    {
      "invoice_number": "INV-006",
      "tone_score": 5,
      "subject": "Friendly Reminder: Invoice INV-006 — Sterling Global Partners",
      "description": "Dear valued partner at Sterling Global, ..."
    }
  ],
  "errors": []
}
```

> **INV-001** (multiple past defaults) gets tone 0 — firm, consequences stated.
> **INV-006** (high-value client) gets tone 5 — warm, relationship-first.

---

### `POST /a2a` — Google A2A JSON-RPC 2.0

For agent-to-agent interoperability. Accepts `tasks/send` and `tasks/get` methods.

```bash
curl -X POST http://localhost:8000/a2a \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "req-1",
    "method": "tasks/send",
    "params": {
      "message": {
        "parts": [{
          "type": "data",
          "data": {
            "invoices": [{
              "invoice_number": "INV-004",
              "company_name": "Harborview Consulting",
              "amount": 5500.00,
              "due_date": "2025-08-20"
            }]
          }
        }]
      }
    }
  }'
```

### `GET /.well-known/agent.json` — A2A Agent Card

Describes this agent's capabilities, skills, and input/output schemas per the A2A spec.

```bash
curl http://localhost:8000/.well-known/agent.json
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key (`sk-...`) |
| `DATABRICKS_HOST` | Yes* | Databricks workspace URL |
| `DATABRICKS_TOKEN` | Yes* | Databricks personal access token |
| `MLFLOW_TRACKING_URI` | No | `databricks` or `http://127.0.0.1:5000` (default: `databricks`) |
| `MLFLOW_EXPERIMENT_NAME` | No | MLflow experiment path (default: `cash-collection-drafter`) |
| `BASE_URL` | No | Public URL of this service (default: `http://localhost:8000`) |
| `LLM_JUDGE_ENABLED` | No | `true` to enable LLM-as-judge scorer (uses extra OpenAI credits) |

\* Required if `MLFLOW_TRACKING_URI=databricks`. Set `MLFLOW_TRACKING_URI=http://127.0.0.1:5000` to use a local MLflow server with no Databricks dependency.

---

## Project Structure

```
cash-collection-a2a/
│
├── app.py                  # FastAPI application — endpoints & MLflow setup
├── crm.py                  # Mock CRM data store (8 records, all tone tiers)
├── mcp_server.py           # MCP STDIO server — exposes fetch_client_by_invoice tool
│
├── crew/
│   ├── email_crew.py       # Three-agent CrewAI pipeline
│   └── tone_rubric.py      # Tone scoring rubric (0-5 scale, with decision guide)
│
├── a2a/
│   ├── agent_card.py       # A2A Agent Card builder (/.well-known/agent.json)
│   └── task_handler.py     # JSON-RPC 2.0 dispatcher — tasks/send, tasks/get
│
├── evaluation/
│   └── scorers.py          # tone_consistency, completeness, guardrail, llm_judge
│
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── DESIGN.md               # Full system design document
```

---

## Evaluation Pipeline

Every drafted email is automatically evaluated by four scorers before the response is returned:

| Scorer | Type | What it checks |
|---|---|---|
| `tone_consistency` | Rule-based | Firm language in score 0-1 emails; polite markers in score 4-5 |
| `completeness_*` | Rule-based | Greeting, invoice reference, amount, call-to-action, sign-off |
| `guardrail_pass` | Rule-based | No offensive, threatening, or abusive content |
| `llm_judge_professional_tone` | LLM-as-judge | Holistic tone appropriateness via MLflow Guidelines scorer |

All metrics are logged to MLflow as numeric values (1.0 = pass, 0.0 = fail) for trend analysis and experiment comparison.

---

## Test Invoices

The mock CRM covers all tone tiers out of the box:

| Invoice | Client | Relationship | Expected Tone |
|---|---|---|---|
| INV-001 | Blackstone Retail Ltd | Multiple past defaults | 0 — Firm |
| INV-002 | Meridian Logistics | Repeat late payer | 1 — Assertive |
| INV-003 | Crestwood Manufacturing | Overdue, no history | 2 — Direct |
| INV-004 | Harborview Consulting | Standard client | 3 — Neutral |
| INV-005 | Apex Innovations Inc | New client | 4 — Courteous |
| INV-006 | Sterling Global Partners | High value client | 5 — Polite |
| INV-007 | Evergreen Tech Solutions | Long-term client | 5 — Polite |
| INV-008 | Cascade Digital Services | Overdue, no history | 2 — Direct |

---

## Design & Architecture

For protocol walkthroughs, agent interaction diagrams, evaluation pipeline design, and architectural trade-offs, see **[DESIGN.md](DESIGN.md)**.
