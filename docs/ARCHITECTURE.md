# Architecture — Data Analysis Chat Assistant

This document describes the production High-Level Design. The prototype in this
repository implements the same architecture end-to-end on lightweight local
substitutes (see [Prototype ↔ Production mapping](#prototype--production-mapping)).

## 1. System overview

```mermaid
flowchart TB
    subgraph Clients
        UI[Web chat / Slack]
        CLI[CLI]
    end

    subgraph GCP["GCP — serverless"]
        GW[API Gateway + IAP\nauthn, rate limiting]

        subgraph AgentSvc["Agent Service — Cloud Run"]
            GUARD[Input guardrail\nintent + injection filter]
            AGENT[Agent runtime\ndeepagents / LangGraph]
            MASK[Output PII masker]
        end

        subgraph LLM["LLM layer"]
            ROUTER[LLM router\nretries, circuit breaker]
            G1[Gemini 2.5 Pro\nprimary]
            G2[Gemini 2.5 Flash\nfallback + judge]
        end

        subgraph Data["Data plane"]
            BQ[(BigQuery\nread-only, byte-capped)]
            GB[(Golden Bucket\nGCS: Q→SQL→Report trios)]
            VS[(Vertex AI\nVector Search)]
            FS[(Firestore\nsessions, prefs,\nsaved reports)]
            CFG[(Persona config\nFirestore + admin UI)]
        end

        subgraph Obs["Observability"]
            LF[Langfuse\ntraces, costs, evals]
            MON[Cloud Monitoring\ndashboards + alerts]
        end

        subgraph Learn["Learning loop"]
            FB[Feedback events\nPub/Sub]
            CUR[Curation pipeline\nCloud Run job]
            REV[Analyst review UI]
        end
    end

    UI --> GW
    CLI --> GW
    GW --> GUARD --> AGENT --> MASK
    AGENT <--> ROUTER
    ROUTER --> G1
    ROUTER -. on failure/429 .-> G2
    AGENT -- SQL guard --> BQ
    AGENT -- retrieve trios --> VS
    VS --- GB
    AGENT <--> FS
    AGENT -- hot reload --> CFG
    AGENT --> LF --> MON
    MASK --> GW
    UI -- 👍/👎, edits --> FB --> CUR --> REV -- publish --> GB
    CUR -- reindex --> VS
```

**Compute**: everything is serverless (Cloud Run) — the agent is stateless
between turns; conversation state lives in Firestore (LangGraph checkpointer),
so any replica can serve any turn and scaling is horizontal per-request.

**Communication**: clients talk HTTPS/SSE (streaming tokens) to the Agent
Service; the agent talks to BigQuery/Firestore/Vector Search via Google client
libraries (IAM service accounts, no keys); feedback flows asynchronously
through Pub/Sub so the learning loop never blocks the chat path.

## 2. Query data flow

```mermaid
sequenceDiagram
    actor M as Manager
    participant A as Agent Service
    participant V as Vector Search (Golden)
    participant L as LLM
    participant B as BigQuery

    M->>A: "Why are Texas users underspending vs California?"
    A->>A: load persona + user preferences into system prompt
    A->>V: embed(question) → top-k similar trios
    V-->>A: analyst examples (Q → SQL → Report)
    A->>L: question + schema + trios + preferences
    L-->>A: tool call: run_sql(sql)
    A->>A: SQL guard: SELECT-only? PII columns? byte cap
    A->>B: execute (read-only SA, maximum_bytes_billed)
    B-->>A: result rows / error / empty
    alt error or empty result
        A->>L: error text as observation (max 3 attempts)
        L-->>A: corrected SQL → retry
    end
    L-->>A: final analysis narrative
    A->>A: output PII mask + trace to Langfuse
    A-->>M: answer (tables/bullets per user preference)
```

## 3. Destructive operations flow

The DB is read-only; the only destructive surface is the Saved Reports library.
Deletion is gated by a **hard interrupt** in the agent graph — the model cannot
execute the tool without an explicit human decision, and the approval is
enforced by the runtime (LangGraph human-in-the-loop), not by prompt goodwill.

```mermaid
sequenceDiagram
    actor M as Manager
    participant A as Agent runtime
    participant S as Reports store

    M->>A: "Delete all reports mentioning Client X"
    A->>S: list_saved_reports (scoped to this user)
    A->>A: model selects matching ids + reason
    A-->>M: ⚠ confirmation dialog: ids, titles, reason
    alt approve
        M->>A: approve
        A->>S: DELETE ids (owner-scoped)
        A-->>M: "Deleted 2 reports, kept 1 (no mention of Client X)"
    else reject
        M->>A: reject
        A-->>M: "Nothing was deleted"
    end
```

UX notes: the flow adds exactly one click for the destructive case and zero
friction anywhere else; users can only ever delete their own reports (ownership
is enforced in the storage layer, not by the model).

## 4. Golden Bucket: retrieval and update loop

**At query time** the question is embedded and the top-k most similar trios are
retrieved and injected into the model context as worked examples. This transfers
analyst conventions the schema alone cannot teach (e.g. "revenue excludes
cancelled/returned items", "decompose 'underspending' into conversion ×
frequency × basket size").

**Over time** the bucket grows through a curated pipeline:

```mermaid
flowchart LR
    T[Chat turn] --> E{Feedback signal}
    E -- 👍 / saved report / exec shared it --> C[Candidate trio\nquestion + SQL + report]
    C --> Q[Review queue]
    Q --> H[Human analyst review\nedit / approve / reject]
    H -- approve --> G[(Golden Bucket)]
    G --> I[Nightly reindex\nVertex Vector Search]
    E -- 👎 / correction --> N[Negative examples\nprompt regression set]
```

Key decisions:
- **Humans stay in the loop**: only analyst-approved trios enter the bucket, so
  one bad LLM answer can never poison future retrievals.
- Negative feedback becomes eval cases, hardening the regression suite.
- Trios are versioned in GCS; the index rebuild is a nightly batch job.

**In the prototype** the capture step of this pipeline is the `/good` CLI
command: it assembles a candidate trio from the last exchange — the user's
question, every SQL query the agent executed for it, and the final report —
and writes it to `golden/candidates/` with `pending_review` metadata.
Retrieval deliberately ignores the candidates folder; the human review step is
moving the (possibly edited) file into `golden/`, after which it is retrieved
on the very next question — no restart, no redeploy. Same gate, same
one-way door as production; only the transport (Pub/Sub, review UI, reindex)
is simplified to a folder move.

## 5. Prototype ↔ Production mapping

| Building block | Prototype (this repo) | Production |
|---|---|---|
| Agent runtime | deepagents + LangGraph, in-process | same code on Cloud Run, SSE streaming |
| LLM | Ollama Cloud (`kimi-k3:cloud`) + fallback model | Gemini 2.5 Pro + Flash fallback on Vertex AI |
| Golden Bucket | `golden/*.json` + lexical retrieval | GCS + embeddings in Vertex AI Vector Search |
| Sessions / checkpoints | in-memory checkpointer | Firestore checkpointer |
| Reports & preferences | SQLite (`data/app.db`) | Firestore (per-user collections) |
| Personas | `personas/*.yaml`, hot-reloaded each turn; editable by chat via `update_persona` (confirmation-gated) | Firestore config + admin UI + same chat editing, same hot reload |
| Observability | JSONL traces + `/trace`, `/metrics`; optional Langfuse (enabled by `.env` keys) | Langfuse + Cloud Monitoring dashboards/alerts |
| Learning-loop capture | `/good` → `golden/candidates/` + manual review (move into `golden/`) | Pub/Sub feedback events → curation job → analyst review UI → GCS publish + reindex |
| QA | `evals/` harness with LLM judge | same suite in CI + canary before rollout |
| Safety | SQL guard + PII deny-list + output masker | same, plus Cloud DLP scan and IAM-scoped SA |

The prototype was intentionally built with the same component boundaries as the
production design — every local substitute sits behind the same interface as
its cloud counterpart, so promotion is a matter of swapping adapters, not
rewriting the agent.
