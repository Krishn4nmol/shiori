# Shiori (栞)

**Cited answers from your research papers.** Upload papers, ask questions in English or
Japanese, and get answers that link to the exact page and passage. It comes with an evaluation
dashboard that measures retrieval and answer quality.

> Status: Phase 1 (ingestion) done. See the roadmap below.

## Stack

| Layer      | Tech                                                                       |
| ---------- | -------------------------------------------------------------------------- |
| Frontend   | React 19, Vite, TypeScript, Tailwind CSS                                    |
| Backend    | FastAPI (Python 3.11, uv), SQLAlchemy (async), Alembic                      |
| Database   | PostgreSQL 17 + pgvector (PGroonga for Japanese keyword search in Phase 2)  |
| Queue      | Redis + background worker (Phase 1)                                         |
| AI         | bge-m3 embeddings (local CPU), bge-reranker, Anthropic API or Ollama           |

## Run locally

Prerequisites: Docker Desktop, Python 3.11 + [uv](https://docs.astral.sh/uv/), Node 22.

```bash
cp .env.example .env              # then add your ANTHROPIC_API_KEY
docker compose up -d              # Postgres + Redis

cd backend
uv sync
uv run alembic upgrade head       # create the database schema
uv run uvicorn app.main:app --reload

# in another terminal: the ingestion worker (first start downloads bge-m3, about 2.3 GB)
cd backend
uv run arq app.worker.WorkerSettings

# in a third terminal
cd frontend
npm install
npm run dev                       # http://localhost:5173
```

API docs: http://localhost:8000/docs

### LLM setup

Shiori uses **Haiku 5.5** (Anthropic) by default ($0.10 / $0.50 per 1M input/output tokens, about
$0.0007 per question). Create an API key at https://console.anthropic.com (API usage is billed
separately from a chat subscription), put it in `.env`, then check it works:

```bash
curl -X POST http://localhost:8000/api/llm/check
```

To run free and offline instead, install [Ollama](https://ollama.com), pull a small model, and set
`LLM_PROVIDER=ollama` and `LLM_MODEL=qwen2.5:3b` in `.env`. Expect slow answers without a GPU.

## How ingestion works

1. **Upload** (`POST /api/documents`): the PDF is checked, de-duplicated by SHA-256, saved
   under `data/uploads/`, and queued in Redis.
2. **Parse** (`app/ingest/pdf_parser.py`): PyMuPDF extracts text with font sizes. Headings come
   from the PDF outline, or from font size, bold text and numbering ("2.1", "第3章"). Running
   headers, footers and page numbers are removed.
3. **Chunk** (`app/ingest/chunker.py`): text is split by section into chunks of about 350 tokens
   with about 50 tokens of sentence overlap. Chunks never cross sections, keep page numbers for
   citations, and skip the reference list.
4. **Embed** (`app/ingest/embedder.py`): bge-m3 runs on CPU. Each chunk is embedded together with
   the paper title and section path, and stored in pgvector with an HNSW index.

The Library page shows live progress. "View chunks" shows exactly what search will see.

## Tests and linting

```bash
cd backend && uv run pytest && uv run ruff check .
cd frontend && npm run lint && npm run build
```

## Roadmap

- [x] **Phase 0**: Setup: repo, Docker, FastAPI, React, CI
- [x] **Phase 1**: Ingestion: PDF parsing, chunking by heading, bge-m3 embeddings, background jobs
- [ ] **Phase 2**: Retrieval: hybrid search (pgvector + PGroonga, merged with RRF) + reranker
- [ ] **Phase 3**: Answers with citations: streaming, chat history, JP ⇄ EN
- [ ] **Phase 4**: Frontend: library, chat, PDF viewer with citation highlights, auth
- [ ] **Phase 5**: Observability: per-query traces (retrieval, prompt, tokens, cost, latency)
- [ ] **Phase 6**: Evaluation dashboard: golden set, recall@k, MRR, faithfulness, experiments
- [ ] **Phase 7**: Agentic routing (LangGraph): query rewrite, multi-step search, self-check
- [ ] **Phase 8**: GraphRAG: entity graph in Neo4j, graph questions, graph view
- [ ] **Phase 9**: Deploy, demo, results write-up
