# System Design — Cash Collection Email Drafter

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [System Overview](#2-system-overview)
3. [Architecture Diagram](#3-architecture-diagram)
4. [Component Deep-Dives](#4-component-deep-dives)
5. [Agent Pipeline](#5-agent-pipeline)
6. [Protocol Implementations](#6-protocol-implementations)
7. [Evaluation Pipeline](#7-evaluation-pipeline)
8. [Data Flow — End to End](#8-data-flow--end-to-end)
9. [Key Design Decisions](#9-key-design-decisions)
10. [Limitations & Future Work](#10-limitations--future-work)

---

## 1. Problem Statement

Accounts-receivable (AR) teams send hundreds of collection emails per week. Writing each email from scratch is slow; using a generic template ignores relationship context and damages client goodwill. The core challenge is **tone calibration** — the right firmness for a serial defaulter is very different from the right warmth for a high-value partner.

**Goal:** Given a batch of invoice records, produce contextually appropriate, personalised collection emails automatically — with quality guarantees.

---

## 2. System Overview

The system is a **FastAPI microservice** that exposes two interfaces:

- **REST (`POST /draft`)** — direct batch processing, synchronous response
- **A2A (`POST /a2a`)** — Google Agent-to-Agent JSON-RPC 2.0 protocol, making this service consumable by any A2A-compatible orchestrator

Each invoice flows through a **three-agent CrewAI pipeline**. The agents communicate via tool calls over the **Model Context Protocol (MCP)** for CRM data retrieval. Every output is evaluated by a **quality scorer pipeline** before results are returned and metrics are persisted to **MLflow / Databricks**.

---

## 3. Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Caller Layer                                     │
│                                                                          │
│   curl / HTTP client          A2A-compatible Agent / Orchestrator        │
│         │                                │                               │
│         │  POST /draft                   │  POST /a2a (JSON-RPC 2.0)     │
└─────────┼───────────────────────────────┼───────────────────────────────┘
          │                               │
          ▼                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Service  (:8000)                         │
│                                                                         │
│  ┌─────────────┐  ┌──────────────────────────┐  ┌──────────────────┐   │
│  │ GET /health │  │ GET /.well-known/         │  │  POST /a2a       │   │
│  └─────────────┘  │       agent.json         │  │  (task_handler)  │   │
│                   │  (A2A Agent Card)         │  └────────┬─────────┘   │
│                   └──────────────────────────┘           │             │
│                                                           │             │
│  ┌────────────────────────────────────────────────────────▼──────────┐  │
│  │                    POST /draft  (DraftRequest)                    │  │
│  │                  MLflow run context wraps each invoice            │  │
│  └─────────────────────────────────┬──────────────────────────────── ┘  │
└────────────────────────────────────┼────────────────────────────────────┘
                                     │  one run_for_invoice() call per invoice
                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       CrewAI  Pipeline                                 │
│                                                                        │
│  ┌──────────────────┐     ┌──────────────────┐     ┌────────────────┐ │
│  │    Agent 1       │     │    Agent 2       │     │   Agent 3      │ │
│  │  CRM Fetcher     │────►│  Tone Analyzer   │────►│ Email Drafter  │ │
│  │                  │     │                  │     │                │ │
│  │ tool: MCP call   │     │ input: tone      │     │ input: CRM +   │ │
│  │   ▼              │     │ rubric (0-5)     │     │ tone_score     │ │
│  │ MCP STDIO        │     │ output: JSON     │     │ output: JSON   │ │
│  │ subprocess       │     │ {tone_score,     │     │ {subject,      │ │
│  │   ▼              │     │  reasoning}      │     │  description}  │ │
│  │ mcp_server.py    │     └──────────────────┘     └────────────────┘ │
│  │   ▼              │                                                  │
│  │  crm.py lookup   │                                                  │
│  └──────────────────┘                                                  │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     Evaluation Pipeline                              │
│                                                                      │
│   tone_consistency   completeness_*   guardrail_pass   llm_judge     │
│         │                 │                │               │         │
│         └─────────────────┴────────────────┴───────────────┘         │
│                                   │                                  │
│                          log_scores_to_mlflow()                      │
└──────────────────────────────────┬───────────────────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────┐
                    │   MLflow / Databricks    │
                    │  experiment tracking +   │
                    │  metric history          │
                    └──────────────────────────┘
```

---

## 4. Component Deep-Dives

### 4.1 `app.py` — FastAPI Application

The entry point. Responsibilities:

- Bootstraps MLflow tracking at startup via `lifespan` context
- Validates all incoming requests with Pydantic models
- Wraps each invoice in a **nested MLflow run** (parent = batch, child = per-invoice) for granular experiment tracking
- Catches per-invoice exceptions and returns partial successes — a single bad invoice does not fail the batch

**Endpoint summary:**

```
GET  /health                  — liveness probe
GET  /.well-known/agent.json  — A2A Agent Card (discovery)
POST /a2a                     — A2A JSON-RPC 2.0 (agent interop)
POST /draft                   — batch invoice processing (direct API)
```

---

### 4.2 `crew/email_crew.py` — Three-Agent Pipeline

A sequential CrewAI crew. Each agent uses `gpt-4o-mini` with `temperature=0.3` for low-variance, consistent outputs.

**Agent 1 — CRM Fetcher**
- Uses the MCP tool `fetch_client_by_invoice` to retrieve the full client record
- Passes the raw record to Agent 2 unchanged — no interpretation at this stage
- `allow_delegation=False` ensures it stays focused on the single retrieval task

**Agent 2 — Tone Analyzer**
- Receives the client record and the full tone rubric text
- Outputs strict JSON: `{"tone_score": int, "reasoning": "str"}`
- Two fallback parsers (JSON → regex) ensure robustness against LLM formatting variance

**Agent 3 — Email Drafter**
- Receives both the CRM record and the decided tone score
- Must produce all four structural elements: greeting, body (invoice + amount + date), call-to-action, sign-off
- Outputs strict JSON: `{"subject": str, "description": str}`
- Same dual-parser fallback as Agent 2

---

### 4.3 `mcp_server.py` — MCP Tool Server

A **FastMCP STDIO server** exposing the `fetch_client_by_invoice` tool.

- Launched as a **subprocess** by CrewAI's `MCPServerAdapter` for each crew run
- Communication is over `stdin/stdout` using the MCP STDIO transport — no network port required
- `PYTHONPATH` is explicitly set to the project root before spawn so `crm.py` (a top-level module) is importable in the subprocess environment
- In production, replace `crm.py` with a real CRM API call inside this server — the CrewAI side requires no changes

---

### 4.4 `crm.py` — Mock CRM

A typed dictionary store (`TypedDict`) with 8 records covering every tone tier from 0 to 5. Designed to be a drop-in replacement target — swap `fetch_client()` with a real HTTP call to Salesforce, HubSpot, or any CRM API.

---

### 4.5 `a2a/` — Agent-to-Agent Protocol

Implements the [Google A2A specification](https://github.com/google-a2a/A2A):

**`agent_card.py`** — builds the Agent Card JSON served at `/.well-known/agent.json`. Contains:
- Agent name, description, version
- Skill definitions with input/output JSON schemas
- Supported transport modes

**`task_handler.py`** — JSON-RPC 2.0 dispatcher with an in-memory task store:

```
Task state machine:

  submitted ──► working ──► completed
                       └──► failed  (only if ALL invoices errored)
```

The A2A endpoint returns the full task object including artifacts, allowing callers to poll via `tasks/get`.

---

### 4.6 `evaluation/scorers.py` — Quality Pipeline

Four scorers, all operating on the final `{invoice_number, tone_score, subject, description}` dict:

| Scorer | Method | Pass condition |
|---|---|---|
| `tone_consistency` | Regex keyword match | Firm markers present for score ≤1; polite markers for score ≥4 |
| `completeness_greeting` | Regex | `dear`, `hello`, or `hi` detected |
| `completeness_invoice_reference` | Regex | Invoice number pattern detected |
| `completeness_amount` | Regex | Dollar amount or "outstanding balance" detected |
| `completeness_call_to_action` | Regex | Payment verb + deadline detected |
| `completeness_sign_off` | Regex | `regards`, `sincerely`, etc. detected |
| `guardrail_pass` | Regex blocklist | No offensive/threatening language |
| `llm_judge_professional_tone` | MLflow Guidelines (LLM) | LLM evaluates holistic appropriateness |

---

## 5. Agent Pipeline

### Sequence Diagram

```
  Caller          FastAPI         CrewAI          MCP Server         CRM
    │                │               │                 │               │
    │─── POST /draft ►│               │                 │               │
    │                │──run_for_inv──►│                 │               │
    │                │               │                 │               │
    │                │               │── spawn subprocess ──────────── │
    │                │               │                 │               │
    │                │   [Agent 1]   │                 │               │
    │                │               │──fetch_client──►│               │
    │                │               │                 │──lookup──────►│
    │                │               │                 │◄── record ────│
    │                │               │◄── client record│               │
    │                │               │                 │               │
    │                │   [Agent 2]   │                 │               │
    │                │               │── analyze tone ─┤ (LLM call)   │
    │                │               │◄─ {tone_score}  │               │
    │                │               │                 │               │
    │                │   [Agent 3]   │                 │               │
    │                │               │── draft email ──┤ (LLM call)   │
    │                │               │◄─ {subject,     │               │
    │                │               │   description}  │               │
    │                │               │                 │               │
    │                │               │── terminate subprocess ──────── │
    │                │◄── result ────│                 │               │
    │                │               │                 │               │
    │                │   [Scorers]   │                 │               │
    │                │── evaluate ───┤                 │               │
    │                │── log MLflow ─┤                 │               │
    │                │               │                 │               │
    │◄── DraftResponse│               │                 │               │
    │                │               │                 │               │
```

---

## 6. Protocol Implementations

### 6.1 Model Context Protocol (MCP)

MCP provides a standardised way for LLM agents to call tools hosted in external processes. This project uses the **STDIO transport**:

```
  CrewAI Agent (MCPServerAdapter)
         │
         │  spawn:  python mcp_server.py
         │          env: PYTHONPATH=/app
         │
         ├──► stdin  ──► FastMCP server reads JSON-RPC tool calls
         └──► stdout ◄── FastMCP server writes JSON-RPC results
```

**Why STDIO over HTTP?**
- Zero network configuration — no port allocation required
- Subprocess lifecycle is tied to the crew run — automatic cleanup
- Eliminates authentication concerns for an internal tool
- Suitable for single-node deployments; upgrade path is MCP over SSE/HTTP for distributed setups

**Tool exposed:**

```python
fetch_client_by_invoice(invoice_number: str) -> dict
# Returns: {invoice_number, client_name, client_email,
#           relationship_info, outstanding_amount, due_date}
# Or:      {"error": "No CRM record found for ..."}
```

---

### 6.2 Google A2A Protocol (Agent-to-Agent)

A2A defines how autonomous agents discover and interact with each other. This service implements two sides of the spec:

**Discovery** — `GET /.well-known/agent.json`

Any A2A-compatible orchestrator can call this endpoint to learn:
- What this agent does
- What input schemas it accepts
- What output schemas it produces
- What authentication it requires (none in this implementation)

**Task Execution** — `POST /a2a`

```
JSON-RPC 2.0 request:
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "method": "tasks/send",    ← or "tasks/get"
  "params": { "message": { "parts": [{ "type": "data", "data": {...} }] } }
}

JSON-RPC 2.0 response:
{
  "jsonrpc": "2.0",
  "id": "req-1",
  "result": {
    "id": "<uuid>",
    "status": { "state": "completed" },
    "artifacts": [{ "name": "drafted_emails", "parts": [...] }]
  }
}
```

**Task states:**

```
submitted ──► working ──► completed   (≥1 invoice succeeded)
                     └──► failed      (all invoices errored)
```

---

## 7. Evaluation Pipeline

### Why Evaluate at Inference Time?

LLMs are non-deterministic. Even with `temperature=0.3`, occasional outputs miss structural elements, slip in the wrong tone markers, or (rarely) produce content that passes the prompt but fails a content policy. By running scorers synchronously on every output:

- Failures are surfaced immediately in the API response (via logs and MLflow metrics)
- Metric trends in MLflow reveal prompt regressions over time
- The guardrail scorer acts as a hard content safety layer

### Scorer Architecture

```
draft output dict
       │
       ├──► tone_consistency_scorer()     ──► {name, value: bool, rationale}
       │
       ├──► completeness_scorer()         ──► [{name, value: bool, rationale}  × 6]
       │       ├── completeness_greeting
       │       ├── completeness_invoice_reference
       │       ├── completeness_amount
       │       ├── completeness_call_to_action
       │       ├── completeness_sign_off
       │       └── completeness_overall
       │
       ├──► guardrail_scorer()            ──► {name, value: bool, rationale}
       │
       └──► _llm_judge_scorer()           ──► {name, value: float|None, rationale}
                (only if LLM_JUDGE_ENABLED=true)
                        │
                        └── MLflow Guidelines scorer
                            (gpt-4o evaluates holistic appropriateness)
```

### MLflow Metric Schema

Each nested run logs these metrics:

```
tone_consistency          1.0 / 0.0
completeness_greeting     1.0 / 0.0
completeness_invoice_reference  1.0 / 0.0
completeness_amount       1.0 / 0.0
completeness_call_to_action     1.0 / 0.0
completeness_sign_off     1.0 / 0.0
completeness_overall      1.0 / 0.0
guardrail_pass            1.0 / 0.0
llm_judge_professional_tone     0.0–1.0  (optional)
```

---

## 8. Data Flow — End to End

### Single invoice through `POST /draft`

```
1.  Request arrives at POST /draft
       └── Pydantic validates: {invoice_number, company_name, amount, due_date}

2.  MLflow parent run starts: "draft-batch"
       └── logs batch_size param

3.  For each invoice, MLflow nested run starts: "draft-{invoice_number}"
       └── logs: invoice_number, company_name, amount, due_date

4.  run_for_invoice(invoice) called:

    a.  CrewAI builds StdioServerParameters
           └── command = sys.executable
               args    = [/app/mcp_server.py]
               env     = {**os.environ, PYTHONPATH: /app}

    b.  MCPServerAdapter spawns mcp_server.py subprocess

    c.  Agent 1 sends MCP tool call: fetch_client_by_invoice(invoice_number)
           └── subprocess looks up crm.py → returns ClientRecord dict

    d.  Agent 2 receives ClientRecord + TONE_RUBRIC text
           └── LLM call → {"tone_score": 0-5, "reasoning": "..."}
           └── Parsed with JSON → regex fallback

    e.  Agent 3 receives ClientRecord + tone_score
           └── LLM call → {"subject": "...", "description": "..."}
           └── Parsed with JSON → regex fallback

    f.  MCP subprocess terminated

5.  run_scorers(result) evaluates the output:
       └── tone_consistency, completeness_*, guardrail_pass

6.  log_scores_to_mlflow(scores) writes metrics to the nested run

7.  mlflow.log_param("tone_score", result["tone_score"])

8.  Nested MLflow run closes

9.  Parent MLflow run closes, logs invoices_processed, invoices_errored

10. DraftResponse returned: {results: [...], errors: [...]}
```

---

## 9. Key Design Decisions

### Sequential vs. Parallel agents
CrewAI's sequential process was chosen deliberately. Each agent's output is the next agent's input — tone analysis requires the CRM record, email drafting requires the tone score. True parallelism isn't applicable here. For independent invoices in a batch, the natural parallelisation point is at the `/draft` endpoint level (future: `asyncio.gather`).

### MCP over direct function call
The CRM fetcher could have called `crm.fetch_client()` directly. Using MCP instead:
- Makes the tool boundary explicit and swappable
- Demonstrates the protocol as it would be used in production (CRM behind an MCP server)
- Allows the MCP server to be replaced with a remote one (SSE/HTTP transport) with zero changes to the crew

### Structured JSON output with dual-parser fallback
LLMs occasionally wrap JSON in markdown code fences or add preamble text. Both Agent 2 and Agent 3 outputs go through: `json.loads()` → regex extraction → graceful default. This makes the pipeline robust to prompt formatting variance without requiring strict output parsers that throw on any deviation.

### Synchronous scorers (no async)
The evaluation scorers are regex-based and run in microseconds. Running them synchronously in the same request thread keeps the architecture simple. The LLM-as-judge scorer is the only one with real latency cost, so it is opt-in via `LLM_JUDGE_ENABLED=true`.

### In-memory task store for A2A
The `_task_store` dict in `task_handler.py` is sufficient for a single-instance demo. For production, this would be replaced with Redis or a PostgreSQL-backed store to support multiple replicas and task persistence across restarts.

### Partial batch success
A single bad invoice (unknown invoice number, LLM parsing failure, etc.) does not fail the entire batch. Errors are collected separately in the `errors` list so the caller gets all successfully drafted emails even if one fails.

---

## 10. Limitations & Future Work

| Area | Current State | Production Path |
|---|---|---|
| CRM data | In-memory mock dict | Replace `crm.py` with real CRM API; keep MCP interface unchanged |
| A2A task store | In-memory dict | Redis / PostgreSQL with TTL-based eviction |
| Concurrency | Synchronous, one invoice at a time | `asyncio.gather` over invoice batch; async CrewAI support |
| Invoice batches | Processed serially | Parallel processing with per-invoice timeout |
| Authentication | None | OAuth2 / API key middleware on FastAPI |
| Streaming | Not supported | Server-sent events for real-time draft streaming |
| MCP transport | STDIO (local subprocess) | MCP over HTTP/SSE for distributed tool servers |
| LLM | `gpt-4o-mini` hardcoded | Config-driven model selection; support for Gemini, Claude, etc. |
| Evaluation | 4 scorers | Add human-in-the-loop feedback loop to MLflow dataset |
