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

Each invoice flows through a **three-agent CrewAI pipeline**. Agent 1 retrieves CRM data via an in-process **FastMCP** tool call. Agent 2 applies a tone rubric to decide the appropriate tone score. Agent 3 drafts the email. Every output is evaluated by a **quality scorer pipeline** and all metrics are persisted to **MLflow / Databricks**.

---

## 3. Architecture Diagram

```mermaid
flowchart TD
    classDef caller  fill:#1e293b,stroke:#0f172a,color:#f8fafc
    classDef api     fill:#1d4ed8,stroke:#1e40af,color:#fff
    classDef agent   fill:#6d28d9,stroke:#5b21b6,color:#fff
    classDef mcp     fill:#065f46,stroke:#064e3b,color:#fff
    classDef eval    fill:#b45309,stroke:#92400e,color:#fff
    classDef mlflow  fill:#4338ca,stroke:#3730a3,color:#fff

    subgraph callers ["  Caller Layer  "]
        H(["HTTP Client\ncurl / Postman"]):::caller
        A(["A2A Agent\nOrchestrator"]):::caller
    end

    subgraph api_sg ["  FastAPI Service :8000  "]
        EP1["GET /health"]:::api
        EP2["GET /.well-known/agent.json\nA2A Agent Card"]:::api
        EP3["POST /a2a\nJSON-RPC 2.0"]:::api
        EP4["POST /draft\nBatch Processing"]:::api
    end

    subgraph crew_sg ["  CrewAI Pipeline  "]
        AG1["Agent 1\nCRM Fetcher"]:::agent
        AG2["Agent 2\nTone Analyzer"]:::agent
        AG3["Agent 3\nEmail Drafter"]:::agent
        AG1 -->|client record| AG2
        AG2 -->|tone_score| AG3
    end

    subgraph mcp_sg ["  FastMCP Server — in-process  "]
        TOOL["fetch_client_by_invoice"]:::mcp
        CRM[("CRM Data\ncrm.py")]:::mcp
        TOOL --> CRM
    end

    subgraph eval_sg ["  Evaluation Pipeline  "]
        S1["tone_consistency"]:::eval
        S2["completeness_*"]:::eval
        S3["guardrail_pass"]:::eval
        S4["llm_judge\noptional"]:::eval
    end

    MLf[("MLflow\nDatabricks")]:::mlflow

    H -->|"POST /draft"| EP4
    A -->|"POST /a2a"| EP3
    EP3 --> EP4
    EP4 --> AG1
    AG1 <-->|FetchClientTool| TOOL
    AG3 -->|"subject + description"| EP4
    EP4 --> S1 & S2 & S3 & S4
    S1 & S2 & S3 & S4 --> MLf
    EP4 -->|DraftResponse| H

    style callers fill:#f1f5f9,stroke:#cbd5e1
    style api_sg  fill:#eff6ff,stroke:#bfdbfe
    style crew_sg fill:#f5f3ff,stroke:#ddd6fe
    style mcp_sg  fill:#f0fdf4,stroke:#bbf7d0
    style eval_sg fill:#fffbeb,stroke:#fde68a
```

---

## 4. Component Deep-Dives

### 4.1 Application Layer — Modular Route Structure

The application is split across focused modules rather than a single monolithic file:

| File | Responsibility |
|---|---|
| `app.py` | Entry point — MLflow bootstrap via `lifespan`, router registration, no business logic |
| `models.py` | Pydantic models — `InvoiceInput`, `DraftRequest`, `DraftResult`, `DraftError`, `DraftResponse` |
| `routes/system.py` | `GET /health`, `GET /docs` (Scalar dark-mode UI), `GET /.well-known/agent.json` |
| `routes/a2a.py` | `POST /a2a` — Google A2A JSON-RPC 2.0 endpoint |
| `routes/draft.py` | `POST /draft` — batch invoice processing, MLflow flat run, scorer logging |

**Endpoint summary:**

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe |
| GET | `/docs` | Scalar API Reference (dark-mode, interactive) |
| GET | `/.well-known/agent.json` | A2A Agent Card (discovery) |
| POST | `/a2a` | A2A JSON-RPC 2.0 (agent interop) |
| POST | `/draft` | Batch invoice → email drafting |

**API Documentation — Scalar**

The default Swagger and ReDoc UIs are disabled. Instead, `routes/system.py` serves a custom **Scalar** API reference page at `/docs`:
- Theme: `saturn` with GitHub-dark CSS palette
- Layout: `modern` with dark mode enabled
- Default HTTP client: Python `requests`
- Pre-filtered to show only relevant language clients

---

### 4.2 `crew/email_crew.py` — Three-Agent Pipeline

A sequential CrewAI crew. Each agent uses `gpt-4o-mini` with `temperature=0.3` for low-variance, consistent outputs.

```mermaid
flowchart LR
    classDef io    fill:#1e293b,stroke:#0f172a,color:#f8fafc
    classDef agent fill:#6d28d9,stroke:#5b21b6,color:#fff

    IN(["invoice_number\ncompany_name\namount\ndue_date"]):::io

    subgraph crew ["  CrewAI Sequential Crew  "]
        A1["Agent 1 — CRM Fetcher\ntool: FetchClientTool\noutput: client record JSON"]:::agent
        A2["Agent 2 — Tone Analyzer\ninput: client record + rubric\noutput: tone_score 0-5 + reasoning"]:::agent
        A3["Agent 3 — Email Drafter\ninput: client record + tone_score\noutput: subject + description"]:::agent
    end

    OUT(["subject\ndescription\ntone_score"]):::io

    IN --> A1 --> A2 --> A3 --> OUT

    style crew fill:#f5f3ff,stroke:#ddd6fe
```

**Agent 1 — CRM Fetcher**
- Uses `FetchClientTool` (a custom CrewAI `BaseTool`) to call the FastMCP server in-process
- Passes the raw client record to Agent 2 unchanged — no interpretation at this stage

**Agent 2 — Tone Analyzer**
- Receives the client record and the full tone rubric text
- Outputs strict JSON: `{"tone_score": int, "reasoning": "str"}`
- Two fallback parsers (JSON → regex) ensure robustness against LLM formatting variance

**Agent 3 — Email Drafter**
- Receives both the CRM record and the decided tone score
- Must produce all four structural elements: greeting, body (invoice + amount + date), call-to-action, sign-off
- Outputs strict JSON: `{"subject": str, "description": str}`

---

### 4.3 `mcp_server.py` — FastMCP Tool Server

A **FastMCP** server object that exposes the `fetch_client_by_invoice` tool.

```mermaid
flowchart LR
    classDef crew fill:#6d28d9,stroke:#5b21b6,color:#fff
    classDef mcp  fill:#065f46,stroke:#064e3b,color:#fff
    classDef crm  fill:#0f766e,stroke:#0d9488,color:#fff

    subgraph proc ["  Same Python Process  "]
        TOOL["FetchClientTool._run()\ncrew/email_crew.py"]:::crew
        CLIENT["fastmcp.Client\nasync context manager"]:::mcp
        SERVER["FastMCP mcp object\nmcp_server.py"]:::mcp
        CRM["crm.fetch_client()\ncrm.py"]:::crm
    end

    TOOL -->|"asyncio.run"| CLIENT
    CLIENT <-->|"in-process call\nno subprocess"| SERVER
    SERVER --> CRM
    CRM -->|"ClientRecord dict"| SERVER
    SERVER -->|"JSON string"| CLIENT
    CLIENT -->|"text"| TOOL

    style proc fill:#f0fdf4,stroke:#bbf7d0
```

- Imported directly into the agent crew — no subprocess, no network port
- Can also run standalone via `python mcp_server.py` (STDIO transport) for external A2A use
- In production, replace `crm.fetch_client()` with a real CRM API call — the crew requires no changes

---

### 4.4 `crm.py` — Mock CRM

A typed dictionary store (`TypedDict`) with 8 records covering every tone tier from 0 to 5. Drop-in replacement target — swap `fetch_client()` with a real HTTP call to Salesforce, HubSpot, or any CRM API.

---

### 4.5 `a2a/` — Agent-to-Agent Protocol

Implements the [Google A2A specification](https://github.com/google-a2a/A2A).

**Task state machine:**

```mermaid
stateDiagram-v2
    [*] --> submitted : tasks/send received
    submitted --> working : processing starts
    working --> completed : 1 or more invoices succeeded
    working --> failed : all invoices errored
    completed --> [*]
    failed --> [*]
```

**`agent_card.py`** — builds the Agent Card JSON served at `/.well-known/agent.json`. Contains agent name, description, skill definitions, input/output schemas, and supported transport modes.

**`task_handler.py`** — JSON-RPC 2.0 dispatcher with an in-memory task store supporting `tasks/send` and `tasks/get`.

---

### 4.6 `evaluation/scorers.py` — Quality Pipeline

Four scorers operating on the final `{invoice_number, tone_score, subject, description}` dict:

| Scorer | Method | Pass condition |
|---|---|---|
| `tone_consistency` | Regex keyword match | Firm markers for score ≤1; polite markers for score ≥4 |
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

```mermaid
sequenceDiagram
    actor Caller
    participant API as FastAPI /draft
    participant MLf as MLflow
    participant A1 as Agent 1<br/>CRM Fetcher
    participant MCP as FastMCP Server
    participant CRM as crm.py
    participant A2 as Agent 2<br/>Tone Analyzer
    participant A3 as Agent 3<br/>Email Drafter
    participant Eval as Scorers

    Caller->>API: POST /draft with invoice list
    API->>MLf: start_run("batch-YYYYMMDD-HHMMSS")
    Note over API,MLf: One flat run for the whole batch

    loop for each invoice
        API->>A1: invoice_number, company, amount, due_date
        A1->>MCP: fetch_client_by_invoice("INV-001")
        MCP->>CRM: fetch_client("INV-001")
        CRM-->>MCP: ClientRecord dict
        MCP-->>A1: JSON string

        A1-->>A2: client record (context)
        Note over A2: Applies TONE_RUBRIC<br/>against relationship_info
        A2-->>A3: tone_score + reasoning (context)

        Note over A3: Drafts email body<br/>calibrated to tone_score
        A3-->>API: subject + description

        API->>Eval: run_scorers(result)
        Eval-->>API: name, value, rationale per scorer
        API->>MLf: MlflowClient.log_metric(run_id, "INV-001/tone_consistency", ...)
        Note over API,MLf: Explicit run_id — bypasses<br/>autolog context interference
    end

    API->>MLf: end batch run
    API-->>Caller: DraftResponse — results + errors
```

---

## 6. Protocol Implementations

### 6.1 Model Context Protocol (MCP) — In-Process

MCP provides a standardised way for LLM agents to call tools. This project uses **FastMCP with in-process transport** — the server object is imported directly, no subprocess or network port needed.

```mermaid
flowchart LR
    classDef crew fill:#6d28d9,stroke:#5b21b6,color:#fff
    classDef mcp  fill:#065f46,stroke:#064e3b,color:#fff
    classDef crm  fill:#0f766e,stroke:#0d9488,color:#fff

    subgraph proc ["  Same Python Process  "]
        AGENT["CrewAI Agent\nFetchClientTool._run()"]:::crew
        CLIENT["fastmcp.Client\nasync context manager"]:::mcp
        SERVER["FastMCP mcp object\nmcp_server.py"]:::mcp
        CRM["crm.fetch_client()"]:::crm

        AGENT -->|asyncio.run| CLIENT
        CLIENT <-->|"in-process call"| SERVER
        SERVER --> CRM
    end

    style proc fill:#f0fdf4,stroke:#bbf7d0
```

**Why in-process over subprocess?**
- `crewai_tools.MCPServerAdapter` uses an interactive `click.confirm()` check for the `mcp` package — in a non-TTY Docker container this raises `click.exceptions.Abort`, crashing the server silently
- In-process removes the subprocess entirely: no spawn latency, no PYTHONPATH wiring, no TTY issues
- The `mcp_server.py` object can still run standalone via STDIO for external use

**Tool exposed:**

```python
fetch_client_by_invoice(invoice_number: str) -> dict
# Returns: {invoice_number, client_name, client_email,
#           relationship_info, outstanding_amount, due_date}
# Or:      {"error": "No CRM record found for ..."}
```

---

### 6.2 Google A2A Protocol (Agent-to-Agent)

A2A defines how autonomous agents discover and interact with each other.

```mermaid
sequenceDiagram
    participant Orch as A2A Orchestrator
    participant Agent as This Service

    Note over Orch,Agent: 1. Discovery
    Orch->>Agent: GET /.well-known/agent.json
    Agent-->>Orch: name, description, skills, inputModes

    Note over Orch,Agent: 2. Task Execution
    Orch->>Agent: POST /a2a — tasks/send with invoice data
    Agent-->>Orch: JSON-RPC result with id, status completed, artifacts

    Note over Orch,Agent: 3. Optional Polling
    Orch->>Agent: POST /a2a — tasks/get with task id
    Agent-->>Orch: status + artifacts
```

---

## 7. Evaluation Pipeline

### Why Evaluate at Inference Time?

LLMs are non-deterministic. Even with `temperature=0.3`, occasional outputs miss structural elements, use the wrong tone markers, or produce borderline content. By running scorers synchronously on every output:

- Failures are surfaced immediately in logs and MLflow metrics
- Metric trends reveal prompt regressions over time without manual inspection
- The guardrail scorer acts as a hard content safety layer

### Scorer Flow

```mermaid
flowchart TD
    classDef input   fill:#1e293b,stroke:#0f172a,color:#f8fafc
    classDef scorer  fill:#b45309,stroke:#92400e,color:#fff
    classDef check   fill:#0f766e,stroke:#0d9488,color:#fff
    classDef pass_f  fill:#065f46,stroke:#064e3b,color:#fff
    classDef neutral fill:#374151,stroke:#1f2937,color:#fff
    classDef mlflow  fill:#4338ca,stroke:#3730a3,color:#fff

    OUT(["Draft Output\ntone_score + subject + description"]):::input

    OUT --> T["tone_consistency_scorer"]:::scorer
    OUT --> C["completeness_scorer"]:::scorer
    OUT --> G["guardrail_scorer"]:::scorer
    OUT --> L["_llm_judge_scorer\nonly if LLM_JUDGE_ENABLED=true"]:::scorer

    subgraph tone_detail ["  Tone Consistency Logic  "]
        T --> T0{"score <= 1?"}
        T0 -->|yes| T1["Check: final notice,\n48 hours, legal action..."]:::check
        T0 -->|no| T2{"score >= 4?"}
        T2 -->|yes| T3["Check: appreciate,\nvalued partner, grateful..."]:::check
        T2 -->|no| T4["Neutral 2-3\nauto-pass"]:::neutral
    end

    subgraph comp_detail ["  Completeness — 5 Checks  "]
        C --> C1["greeting"]:::check
        C --> C2["invoice_reference"]:::check
        C --> C3["amount"]:::check
        C --> C4["call_to_action"]:::check
        C --> C5["sign_off"]:::check
        C1 & C2 & C3 & C4 & C5 --> C6["completeness_overall"]:::pass_f
    end

    T & C6 & G & L --> MLf[("MLflow Metrics\n1.0 = pass / 0.0 = fail")]:::mlflow

    style tone_detail fill:#fffbeb,stroke:#fde68a
    style comp_detail fill:#f0fdf4,stroke:#bbf7d0
```

### MLflow Run Structure — Flat Batch Run

Each call to `POST /draft` produces a **single flat MLflow run** named `batch-{YYYYMMDD-HHMMSS}`. All per-invoice metrics are namespaced within that run using the invoice number as a prefix:

```
INV-001/tone_consistency                  1.0 / 0.0
INV-001/completeness_greeting             1.0 / 0.0
INV-001/completeness_invoice_reference    1.0 / 0.0
INV-001/completeness_amount               1.0 / 0.0
INV-001/completeness_call_to_action       1.0 / 0.0
INV-001/completeness_sign_off             1.0 / 0.0
INV-001/completeness_overall              1.0 / 0.0
INV-001/guardrail_pass                    1.0 / 0.0
INV-001/llm_judge_professional_tone       0.0–1.0  (optional)

INV-006/tone_consistency                  ...
```

**Why flat over nested?**

The previous design used a parent run with nested child runs (one child per invoice). MLflow's `mlflow.crewai.autolog()` — enabled for tracing — interferes with the active run context during `crew.kickoff()`, causing nested metrics to land on the wrong run or fail silently. The flat design solves this by:

1. Opening a single run before the loop begins
2. Using `MlflowClient.log_metric(run_id, ...)` to target the run explicitly by ID, bypassing the active-run context stack entirely
3. Namespacing metrics per invoice so all data is visible in one Databricks UI row

---

## 8. Data Flow — End to End

```mermaid
flowchart TD
    classDef entry   fill:#1e293b,stroke:#0f172a,color:#f8fafc
    classDef api     fill:#1d4ed8,stroke:#1e40af,color:#fff
    classDef mlflow  fill:#4338ca,stroke:#3730a3,color:#fff
    classDef agent   fill:#6d28d9,stroke:#5b21b6,color:#fff
    classDef parse   fill:#374151,stroke:#1f2937,color:#fff
    classDef eval    fill:#b45309,stroke:#92400e,color:#fff

    REQ(["POST /draft\ninvoice_number, company_name, amount, due_date"]):::entry

    REQ --> VAL["Pydantic Validation"]:::api
    VAL --> PR["MLflow flat batch run\nbatch-YYYYMMDD-HHMMSS"]:::mlflow

    PR --> INV["run_for_invoice (per invoice)"]:::api

    subgraph pipeline ["  CrewAI Pipeline  "]
        INV --> F["Agent 1 — CRM Fetcher\nfetch_client_by_invoice via FastMCP"]:::agent
        F --> T["Agent 2 — Tone Analyzer\napply TONE_RUBRIC — tone_score 0-5"]:::agent
        T --> D["Agent 3 — Email Drafter\ndraft subject + description"]:::agent
    end

    D --> PARSE["Parse output JSON\nfallback: regex"]:::parse
    PARSE --> SCORE["run_scorers\ntone + completeness + guardrail"]:::eval
    SCORE --> LOG["MlflowClient.log_metric(run_id,\n'INV-001/tone_consistency', ...)"]:::mlflow
    LOG --> CLOSE["Close batch run"]:::mlflow
    CLOSE --> RESP(["DraftResponse\nresults + errors"]):::entry

    style pipeline fill:#f5f3ff,stroke:#ddd6fe
```

---

## 9. Key Design Decisions

### Modular Route Structure

The original `app.py` held all endpoints, models, and MLflow logic in a single file (~370 lines). This was split into:

- `models.py` — all Pydantic schemas in one place, importable by any module
- `routes/system.py` — infrastructure endpoints (health, docs, agent card) kept separate from business logic
- `routes/draft.py` — the core `/draft` handler with all MLflow instrumentation
- `routes/a2a.py` — A2A protocol handler isolated from REST concerns
- `app.py` — thin entry point: `_setup_mlflow()`, `lifespan`, and three `include_router()` calls

Each route file is independently testable and navigable. Patch paths in tests reflect the actual module (`routes.draft.run_for_invoice`) rather than the app namespace.

### Flat MLflow Run vs. Nested Runs

Nested parent/child runs were the initial design. `mlflow.crewai.autolog()` enables tracing during `crew.kickoff()`, which interferes with the MLflow active run context in some versions — causing `mlflow.log_metric()` inside `log_scores_to_mlflow()` to target the autolog trace run rather than the intended child run. The fix: replace context-dependent `mlflow.log_metric()` calls with `MlflowClient.log_metric(run_id, ...)`, which targets a specific run by ID regardless of what the active context is. This also simplifies the run structure from two levels to one.

### Sequential vs. Parallel Agents
CrewAI's sequential process was chosen deliberately. Each agent's output is the next agent's input — tone analysis requires the CRM record, email drafting requires the tone score. True parallelism isn't applicable within a single invoice. For batches, the natural parallelisation point is at the `/draft` endpoint level (future: `asyncio.gather` over invoices).

### In-Process MCP over Subprocess
The CRM tool could have called `crm.fetch_client()` directly. Using FastMCP in-process:
- Makes the tool boundary explicit and swappable (replace the CRM impl without touching the crew)
- Eliminates the TTY/Abort issue caused by `crewai_tools.MCPServerAdapter` in Docker
- The `mcp_server.py` object can still serve external callers via STDIO — no duplication

### Structured JSON Output with Dual-Parser Fallback
LLMs occasionally wrap JSON in markdown code fences or add preamble text. Both Agent 2 and Agent 3 outputs go through: `json.loads()` → regex extraction → graceful default. This makes the pipeline robust to prompt formatting variance without requiring strict output parsers that throw on any deviation.

### Synchronous Scorers (No Async)
The evaluation scorers are regex-based and run in microseconds. Running them synchronously in the same request thread keeps the architecture simple. The LLM-as-judge scorer is the only one with real latency cost, so it is opt-in via `LLM_JUDGE_ENABLED=true`.

### In-Memory Task Store for A2A
The `_task_store` dict in `task_handler.py` is sufficient for a single-instance demo. For production, replace with Redis or a PostgreSQL-backed store to support multiple replicas and task persistence across restarts.

### Partial Batch Success
A single bad invoice (unknown invoice number, LLM parsing failure, etc.) does not fail the entire batch. Errors are collected separately in the `errors` list so the caller gets all successfully drafted emails even if one fails.

---

## 10. Limitations & Future Work

| Area | Current State | Production Path |
|---|---|---|
| CRM data | In-memory mock dict | Replace `crm.py` with real CRM API; MCP interface stays unchanged |
| A2A task store | In-memory dict | Redis / PostgreSQL with TTL-based eviction |
| Concurrency | Synchronous, one invoice at a time | `asyncio.gather` over invoice batch |
| Authentication | None | OAuth2 / API key middleware on FastAPI |
| Streaming | Not supported | Server-sent events for real-time draft streaming |
| LLM | `gpt-4o-mini` hardcoded | Config-driven model selection; Claude, Gemini, Llama support |
| Evaluation | 4 scorers | Add human-in-the-loop feedback loop to MLflow dataset |
| MCP transport | In-process | MCP over HTTP/SSE for distributed tool servers |
