# SupportPilot

A customer-support AI agent for a fake Pakistani electronics store (ShopEase). Practice build on fake data.

## Stack
- n8n AI Agent (brain) with Postgres tools and a RAG knowledge base tool
- Supabase Postgres + pgvector (Mistral embeddings, 1024 dims)
- Website chat widget (`widget/`), Telegram used only for staff alerts

## Folders
- `db/` schema, seed data, numbered migrations, KB ingestion script
- `kb/` 11 policy documents (the agent's knowledge base)
- `eval/` 29-case golden set and 16-attack adversarial set, with scorers
- `widget/` embeddable chat widget and demo page

## Setup
1. Run the SQL files in `db/` in order: `schema.sql`, `002`, `003_kb_1024.sql`, `004` to `007`.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill it in.
4. `python db/seed.py` (WARNING: truncates tables, use a dev database only).
5. `python db/ingest_kb.py`
6. Build the n8n workflows and set `data-api` in the widget to your webhook base URL.

## Security design
- Customer id is injected by the workflow, never chosen by the model; SQL filters on it and fails closed.
- The model only proposes actions; code confirms YES/NO and runs locked, idempotent SQL functions.
- Output guard blocks leaked internals, false "done" claims, promised compensation, unknown order numbers.
- Input cap, rate limit, deterministic escalation to a human.

## Known weaknesses
- Web verification (email + order number) is guessable; lockout is per session; CORS is open.
- The eval and attack suites are written but results are not yet published.
