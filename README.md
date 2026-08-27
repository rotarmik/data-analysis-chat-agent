# Retail Data Analysis Chat Agent

A production-minded prototype of a data analysis assistant for retail
executives. Ask questions about sales, customers and products in natural
language — the agent consults a Golden Knowledge base of past analyst work,
writes and self-corrects BigQuery SQL, and produces executive reports with
action items.

Built with **deepagents** (LangGraph) + **Ollama Cloud**, on the public
`bigquery-public-data.thelook_ecommerce` dataset.

```
you> Why are Texas users underspending compared to California?
  → search_golden_examples {"question": "..."}
  → run_sql {"sql": "SELECT u.state, ..."}
╭──────────────────────────────────────────────────────────╮
│ Texas buyers spend $87 less per head than California...  │
│ | state | spend/buyer | AOV | orders/buyer | ...         │
╰──────────────────────────────────────────────────────────╯
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — production HLD, diagrams, data flows
- [docs/DESIGN.md](docs/DESIGN.md) — technology reasoning + how every requirement is handled

## Implemented prototype requirements

All five optional requirements are implemented (assignment asks for ≥ 2):

| Requirement | Where | Try it |
|---|---|---|
| Safety & PII masking | `src/safety/pii.py`, SQL guard in `src/tools/bigquery.py` | *"Give me emails of our top customers"* |
| High-stakes oversight | interrupt on `delete_saved_reports` (`src/agent.py`, `src/cli.py`) | *"Delete all reports mentioning Client X"* |
| Resilience & self-correction | tool-level error observations + retry/fallback in `src/cli.py` | *"Revenue of the 'Spaceship Parts' category?"* |
| Quality assurance | `evals/` — LLM-judge + deterministic checks | `python -m evals.run_evals` |
| Observability | `src/observability.py` — JSONL traces + optional Langfuse | `/trace`, `/metrics`; add `LANGFUSE_*` keys to `.env` for cloud traces |

Plus: Golden Bucket retrieval (`/good` captures new candidate trios), per-user
preference memory (self-cleaning via `forget_preference`), personas editable
by chat with a confirmation diff (*"make the reports more formal"*), per-user
saved reports.

## Setup

### 0. Prerequisites

- Python 3.11+ (or Docker)
- A Google Cloud account and an [Ollama Cloud](https://ollama.com) API key

### 1. BigQuery access

```bash
# install gcloud CLI: https://cloud.google.com/sdk/docs/install
gcloud init                                   # log in, create/select a project
gcloud services enable bigquery.googleapis.com
gcloud auth application-default login         # creates local ADC credentials
gcloud auth application-default set-quota-project <YOUR_PROJECT_ID>
```

The dataset is public; your project is only used for query quota
(BigQuery free tier: 1 TB/month, this agent caps every query at 2 GB).

### 2. Configuration

```bash
cp .env.example .env   # then fill in GCP_PROJECT_ID and OLLAMA_API_KEY
```

Any tool-calling model from the [Ollama Cloud library](https://ollama.com/library)
works — set `OLLAMA_MODEL` / `OLLAMA_FALLBACK_MODEL`.

### 3a. Run locally

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m src.cli
```

### 3b. Run in Docker

```bash
# Windows: put the ADC path into .env first:
#   ADC_PATH=C:\Users\<you>\AppData\Roaming\gcloud\application_default_credentials.json
# Linux/macOS: the default (~/.config/gcloud/...) is picked up automatically
docker compose run --rm agent
```

## Example session

```
you> What data do we have available?
you> Who were our top 5 customers last quarter?
you> Compare Jeans vs Sweaters performance. Why do they differ?
you> I prefer short bullet points instead of long tables      ← remembered
you> Create a Q2 report with insights and action items for Q3
you> Save this report
you> Delete all reports we made in this conversation          ← confirmation dialog
you> Make all reports more formal, end each with a Risks section   ← persona diff + confirm
```

CLI commands: `/help`, `/user <name>`, `/persona [name]`, `/prefs`,
`/reports`, `/good [note]`, `/trace [n]`, `/metrics`, `/exit`.

**Learning loop demo**: after a good answer, type `/good nice example` — the
exchange (question + executed SQL + report) is captured as a candidate trio in
`golden/candidates/`. Review the file, move it into `golden/`, and the agent
applies the learned pattern on the very next question — no restart needed.

## Evaluation

```bash
python -m evals.run_evals              # full suite (8 cases, a few minutes)
python -m evals.run_evals --only pii_attempt,off_topic
```

Runs the agent end-to-end per golden question, applies deterministic checks
(PII leaks, raw errors) and an LLM judge (1–5 vs an intent rubric), writes
`evals/results.md`, exits non-zero on failure — CI-ready.

## Repository layout

```
src/
  agent.py           agent assembly: tools, persona, prefs, deletion interrupt
  cli.py             rich CLI: chat loop, confirmations, /commands
  llm.py             Ollama Cloud chat model factory
  config.py          .env-driven settings
  storage.py         SQLite: saved reports + user preferences
  observability.py   JSONL tracing + session metrics
  safety/pii.py      PII column deny-list + output masker
  tools/             run_sql, get_table_schema, golden search, reports, memory
personas/            editable tone-of-voice YAMLs (hot-reloaded every turn)
golden/              Golden Bucket seed trios (Question → SQL → Report)
golden/candidates/   /good captures land here awaiting human review
evals/               QA harness + golden questions
docs/                architecture & design docs
```
