# Shiori (栞)

**Cited answers from your research papers.** Upload papers, ask questions in English or
Japanese, and get answers that link to the exact page and passage. It comes with an evaluation
dashboard that measures retrieval and answer quality.

> Status: Phase 3 (answers with citations) done. See the roadmap below.

## Stack

| Layer      | Tech                                                                       |
| ---------- | -------------------------------------------------------------------------- |
| Frontend   | React 19, Vite, TypeScript, Tailwind CSS                                    |
| Backend    | FastAPI (Python 3.11, uv), SQLAlchemy (async), Alembic                      |
| Database   | PostgreSQL 17 + pgvector, full-text index with EN words + JA bigrams     |
| Queue      | Redis + background worker (Phase 1)                                         |
| AI         | bge-m3 embeddings, bge-reranker-v2-m3 (local CPU), Anthropic API or Ollama |

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

## How search works

`POST /api/search` (and the **Search** tab) runs three steps:

1. **Vector search**: the question is embedded with bge-m3 and compared with every chunk by
   cosine similarity (pgvector HNSW index). This finds passages with the same meaning, even in
   the other language: a Japanese question can rank an English passage first.
2. **Keyword search**: exact terms through a Postgres GIN index. English is indexed as words;
   Japanese as overlapping 2-character pieces plus single kanji (鳥取砂丘 → 鳥取, 取砂, 砂丘),
   with hiragana-only pieces dropped (grammar and furigana). This catches names, numbers and
   rare terms that vectors blur. It does the job PGroonga would, without a custom Postgres build.
3. **Fusion and reranking**: the two ranked lists are merged with Reciprocal Rank Fusion
   (`1 / (60 + rank)`), then the top 12 are re-scored by the bge-reranker-v2-m3
   cross-encoder (bfloat16 on CPU, about 3 s).

4. **Relevance cutoff**: reranked results scoring below `MIN_RERANK_SCORE` (default 0.01) are
   hidden. On real queries, right answers scored 0.05–0.8 and unrelated passages below 0.001,
   so a question nothing answers ("What is the weather in Osaka?") returns
   "No passage in your papers answers this" instead of 8 unrelated passages.

Each result reports its vector rank, keyword rank, fused score, rerank score and per-stage
timings, so ranking decisions can be inspected and later evaluated (Phase 6).

## How answers work

The **Ask** tab (`POST /api/chat`, streamed as Server-Sent Events):

1. **Follow-ups are rewritten** into standalone questions using the conversation
   ("and for how long?" → "How long were the dune sites measured?").
2. **Hybrid search** finds up to 6 passages. If none pass the relevance cutoff, Shiori replies
   that the papers don't answer this, **without calling the LLM** (no cost, nothing invented).
3. **The LLM answers only from the numbered passages**, cites each claim as `[n]`, and replies
   in the language of the question (prompts in `app/answer/prompts.py`). Passage text is treated
   as data, not instructions.
4. **The answer streams** to the browser. Citations become links to the source passage and PDF
   page, and each answer records its sources, tokens, cost and per-stage timings.

## Tests and linting

```bash
cd backend && uv run pytest && uv run ruff check .
cd frontend && npm run lint && npm run build
```

## Roadmap

- [x] **Phase 0**: Setup: repo, Docker, FastAPI, React, CI
- [x] **Phase 1**: Ingestion: PDF parsing, chunking by heading, bge-m3 embeddings, background jobs
- [x] **Phase 2**: Retrieval: hybrid search (pgvector + keyword index, merged with RRF) + reranker
- [x] **Phase 3**: Answers with citations: streaming, chat history, JP ⇄ EN
- [ ] **Phase 4**: Frontend: library, chat, PDF viewer with citation highlights, auth
- [ ] **Phase 5**: Observability: per-query traces (retrieval, prompt, tokens, cost, latency)
- [ ] **Phase 6**: Evaluation dashboard: golden set, recall@k, MRR, faithfulness, experiments
- [ ] **Phase 7**: Agentic routing (LangGraph): query rewrite, multi-step search, self-check
- [ ] **Phase 8**: GraphRAG: entity graph in Neo4j, graph questions, graph view
- [ ] **Phase 9**: Deploy, demo, results write-up
