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
    subgraph callers [Caller Layer]
        H([HTTP Client\ncurl / Postman])
        A([A2A Agent\nOrchestrator])
    end

    subgraph api [FastAPI Service :8000]
        EP1[GET /health]
        EP2[GET /.well-known/agent.json\nA2A Agent Card]
        EP3[POST /a2a\nJSON-RPC 2.0]
        EP4[POST /draft\nBatch Processing]
    end

    subgraph crew [CrewAI Pipeline]
        AG1[Agent 1\nCRM Fetcher]
        AG2[Agent 2\nTone Analyzer]
        AG3[Agent 3\nEmail Drafter]
        AG1 -->|client record| AG2
        AG2 -->|tone_score| AG3
    end

    subgraph mcp [FastMCP Server — in-process]
        TOOL[fetch_client_by_invoice]
        CRM[(CRM Data\ncrm.py)]
        TOOL --> CRM
    end

    subgraph eval [Evaluation Pipeline]
        S1[tone_consistency]
        S2[completeness_*]
        S3[guardrail_pass]
        S4[llm_judge\noptional]
    end

    MLf[(MLflow\nDatabricks)]

    H -->|POST /draft| EP4
    A -->|POST /a2a| EP3
    EP3 --> EP4
    EP4 --> AG1
    AG1 <-->|FetchClientTool| TOOL
    AG3 -->|subject + description| EP4
    EP4 --> S1 & S2 & S3 & S4
    S1 & S2 & S3 & S4 --> MLf
    EP4 -->|DraftResponse| H
```

---

## 4. Component Deep-Dives

### 4.1 `app.py` — FastAPI Application

The entry point. Responsibilities:

- Bootstraps MLflow tracking at startup via `lifespan` context manager
- Validates all incoming requests with Pydantic models
- Wraps each invoice in a **nested MLflow run** (parent = batch, child = per-invoice) for granular experiment tracking
- Catches per-invoice exceptions and returns partial successes — a single bad invoice does not fail the batch

**Endpoint summary:**

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness probe |
| GET | `/.well-known/agent.json` | A2A Agent Card (discovery) |
| POST | `/a2a` | A2A JSON-RPC 2.0 (agent interop) |
| POST | `/draft` | Batch invoice → email drafting |

---

### 4.2 `crew/email_crew.py` — Three-Agent Pipeline

A sequential CrewAI crew. Each agent uses `gpt-4o-mini` with `temperature=0.3` for low-variance, consistent outputs.

```mermaid
flowchart LR
    IN([invoice_number\ncompany_name\namount\ndue_date])

    subgraph crew [CrewAI Sequential Crew]
        A1["Agent 1\nCRM Fetcher\n─────────────\ntool: FetchClientTool\noutput: client record JSON"]
        A2["Agent 2\nTone Analyzer\n─────────────\ninput: client record + rubric\noutput: {tone_score, reasoning}"]
        A3["Agent 3\nEmail Drafter\n─────────────\ninput: client record + tone_score\noutput: {subject, description}"]
    end

    OUT([subject\ndescription\ntone_score])

    IN --> A1 --> A2 --> A3 --> OUT
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
    TOOL["FetchClientTool._run()\nin crew/email_crew.py"]
    CLIENT["fastmcp.Client\nasync context manager"]
    SERVER["FastMCP server object\nmcp_server.py"]
    CRM["crm.fetch_client()\ncrm.py"]

    TOOL -->|asyncio.run| CLIENT
    CLIENT <-->|in-process\nno subprocess| SERVER
    SERVER --> CRM
    CRM -->|ClientRecord dict| SERVER
    SERVER -->|JSON string| CLIENT
    CLIENT -->|text| TOOL
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
    working --> completed : ≥1 invoice succeeded
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

    Caller->>API: POST /draft {invoices: [...]}
    API->>MLf: start_run("draft-batch")
    API->>MLf: start_run("draft-INV-001", nested)

    API->>A1: invoice_number, company, amount, due_date
    A1->>MCP: fetch_client_by_invoice("INV-001")
    MCP->>CRM: fetch_client("INV-001")
    CRM-->>MCP: ClientRecord dict
    MCP-->>A1: JSON string

    A1-->>A2: client record (context)
    Note over A2: Applies TONE_RUBRIC<br/>against relationship_info
    A2-->>A3: {tone_score, reasoning} (context)

    Note over A3: Drafts email body<br/>calibrated to tone_score
    A3-->>API: {subject, description}

    API->>Eval: run_scorers(result)
    Eval-->>API: [{name, value, rationale}, ...]
    API->>MLf: log_metric(tone_consistency, completeness_*, guardrail_pass)
    API->>MLf: end nested run
    API->>MLf: end parent run

    API-->>Caller: DraftResponse {results, errors}
```

---

## 6. Protocol Implementations

### 6.1 Model Context Protocol (MCP) — In-Process

MCP provides a standardised way for LLM agents to call tools. This project uses **FastMCP with in-process transport** — the server object is imported directly, no subprocess or network port needed.

```mermaid
flowchart LR
    subgraph crew_process [Same Python Process]
        AGENT["CrewAI Agent\nFetchClientTool._run()"]
        CLIENT["fastmcp.Client\n(async context manager)"]
        SERVER["FastMCP mcp object\nmcp_server.py"]
        CRM["crm.fetch_client()"]

        AGENT -->|asyncio.run| CLIENT
        CLIENT <-->|in-process call| SERVER
        SERVER --> CRM
    end
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
    Agent-->>Orch: {name, description, skills, inputModes}

    Note over Orch,Agent: 2. Task Execution
    Orch->>Agent: POST /a2a {jsonrpc:"2.0", method:"tasks/send", params:{...}}
    Agent-->>Orch: {jsonrpc:"2.0", result:{id, status:{state:"completed"}, artifacts:[...]}}

    Note over Orch,Agent: 3. Optional Polling
    Orch->>Agent: POST /a2a {method:"tasks/get", params:{id:"..."}}
    Agent-->>Orch: {result:{status, artifacts}}
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
    OUT([Draft Output\ntone_score + subject + description])

    OUT --> T[tone_consistency_scorer]
    OUT --> C[completeness_scorer]
    OUT --> G[guardrail_scorer]
    OUT --> L[_llm_judge_scorer\nonly if LLM_JUDGE_ENABLED=true]

    subgraph tone_detail [Tone Consistency Logic]
        T --> T0{score ≤ 1?}
        T0 -->|yes| T1[Check: final notice,\n48 hours, legal action...]
        T0 -->|no| T2{score ≥ 4?}
        T2 -->|yes| T3[Check: appreciate,\nvalued partner, grateful...]
        T2 -->|no| T4[Neutral 2–3\nauto-pass]
    end

    subgraph comp_detail [Completeness — 5 Checks]
        C --> C1[greeting]
        C --> C2[invoice_reference]
        C --> C3[amount]
        C --> C4[call_to_action]
        C --> C5[sign_off]
        C1 & C2 & C3 & C4 & C5 --> C6[completeness_overall]
    end

    T & C6 & G & L --> MLf[(MLflow Metrics\n1.0 = pass / 0.0 = fail)]
```

### MLflow Metric Schema

Each nested run logs:

```
tone_consistency                  1.0 / 0.0
completeness_greeting             1.0 / 0.0
completeness_invoice_reference    1.0 / 0.0
completeness_amount               1.0 / 0.0
completeness_call_to_action       1.0 / 0.0
completeness_sign_off             1.0 / 0.0
completeness_overall              1.0 / 0.0
guardrail_pass                    1.0 / 0.0
llm_judge_professional_tone       0.0–1.0  (optional)
```

---

## 8. Data Flow — End to End

```mermaid
flowchart TD
    REQ([POST /draft\n{invoice_number, company_name, amount, due_date}])

    REQ --> VAL[Pydantic Validation]
    VAL --> PR[MLflow parent run\ndraft-batch]
    PR --> NR[MLflow nested run\ndraft-invoice_number]

    NR --> INV[run_for_invoice]

    subgraph pipeline [CrewAI Pipeline]
        INV --> F[Agent 1: fetch_client_by_invoice\nvia FastMCP in-process]
        F --> T[Agent 2: apply TONE_RUBRIC\nLLM → tone_score 0-5]
        T --> D[Agent 3: draft email\nLLM → subject + description]
    end

    D --> PARSE[Parse output JSON\nfallback: regex]
    PARSE --> SCORE[run_scorers\ntone + completeness + guardrail]
    SCORE --> LOG[log_scores_to_mlflow]
    LOG --> CLOSE[Close nested run\nClose parent run]
    CLOSE --> RESP([DraftResponse\n{results, errors}])
```

---

## 9. Key Design Decisions

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
