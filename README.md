# Shiori (栞)

**Cited answers from your research papers.** Upload papers, ask questions in English or
Japanese, and get answers that link to the exact page and passage. Built for a university lab:
hybrid search, a LangGraph agent, a Neo4j knowledge graph, observability and an evaluation
dashboard, running free (local models + Gemini's free tier).

**Results and design decisions: [docs/RESULTS.md](docs/RESULTS.md)**: tested on real arXiv
papers and a Japanese research report, where evaluation exposed six PDF-parsing bugs and moved
top-5 search from 61% to 74%.

## Stack

| Layer      | Tech                                                                       |
| ---------- | -------------------------------------------------------------------------- |
| Frontend   | React 19, Vite, TypeScript, Tailwind CSS                                    |
| Backend    | FastAPI (Python 3.11, uv), SQLAlchemy (async), Alembic                      |
| Database   | PostgreSQL 17 + pgvector, full-text index with EN words + JA bigrams; Neo4j 5 (knowledge graph) |
| Queue      | Redis + background worker (Phase 1)                                         |
| AI         | bge-m3 + bge-reranker-v2-m3 (local CPU); Gemini free tier, Ollama, or any OpenAI-compatible LLM |

## Run locally

Prerequisites: Docker Desktop, Python 3.11 + [uv](https://docs.astral.sh/uv/), Node 22.

```bash
cp .env.example .env              # then add your LLM_API_KEY
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

### LLM setup (free)

Search and embeddings run locally for free. Only written answers (the **Ask** tab) need an LLM,
and the default is **Google Gemini's free tier** (`gemini-3.5-flash-lite`, about 500 requests a
day, no credit card):

1. Create a key at https://aistudio.google.com/apikey
2. Put it in `.env` as `LLM_API_KEY=...`
3. Restart the API and check it works: `curl -X POST http://localhost:8000/api/llm/check`

Free-tier prompts may be used by Google to improve its products, so for confidential papers use
the **local** option instead: install [Ollama](https://ollama.com), run `ollama pull qwen3:4b`,
and switch to the Ollama block in `.env` (private and unlimited, but slower without a GPU).
Groq and the paid Anthropic API are also preconfigured in `.env.example`. Any
OpenAI-compatible API works by changing `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY`.

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
   the other language: 「RAG は文書の検索インデックスを作るのに何を使っていますか？」 ranks the English
   passage "build a single MIPS index using FAISS" first.
2. **Keyword search**: exact terms through a Postgres GIN index. English is indexed as words;
   Japanese as overlapping 2-character pieces plus single kanji (鳥取砂丘 → 鳥取, 取砂, 砂丘),
   with hiragana-only pieces dropped (grammar and furigana). This catches names, numbers and
   rare terms that vectors blur. It does the job PGroonga would, without a custom Postgres build.
3. **Fusion and reranking**: the two ranked lists are merged with Reciprocal Rank Fusion
   (`1 / (60 + rank)`), then the top 12 are re-scored by the bge-reranker-v2-m3
   cross-encoder (bfloat16 on CPU, about 5 s). The reranker sees each passage with its paper
   title and section, because a short passage often names its topic only in its heading.

4. **Relevance cutoff**: reranked results scoring below `MIN_RERANK_SCORE` (default 0.0003) are
   hidden, so a question nothing answers returns "No passage in your papers answers this"
   instead of unrelated passages. The value was chosen by evaluation (below): at 0.01, two more
   of the 23 answerable test questions lost the passage that answers them.

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
4. **The answer streams** to the browser. Each answer records its sources, tokens, cost and
   per-stage timings.
5. **Citations open the PDF in-app** (pdf.js) at the cited page with the passage highlighted.
   The page's text items are matched against the passage with overlapping 12-character windows,
   so tiny text fragments and common words elsewhere on the page are not highlighted.

## Observability (Insights tab, admins)

Every question and search writes a **trace**: the rewritten query, every passage considered
with its vector rank, keyword rank and rerank score, whether the cutoff kept it and whether the
answer cited it, plus tokens, cost, per-stage timings and the prompt version (a hash of the
system prompt, so quality can be compared across prompt changes).

The **Insights** tab shows answered rate, typical and slowest-5% answer time (p50/p95), where
the time goes per stage, cost, 👍/👎 counts, questions per day, and recent questions nothing
answered. Every trace opens into a timing waterfall and the full candidate table. Readers rate
answers with 👍/👎 (and an optional note) on the Ask page.

Admins can read the questions people asked, which is needed to find and fix weak answers.
Conversations themselves stay private on the Ask page.

## The answer agent (LangGraph)

One fixed path (search, top 6 passages, answer) is a poor fit for some questions: "Who wrote
the DPR paper?" (the authors are on the title page, which no topic search looks for), "List all
the datasets used" (needs the whole paper, not six passages), comparisons and summaries. With
`ANSWER_MODE=agent` (default) each question is planned first:

```
plan ──▶ details   paper metadata (title, authors, pages…) as citable sources
     ├─▶ search    hybrid search over 1-3 queries, e.g. the question plus its Japanese
     │             translation, merged with RRF and reranked once (RAG-Fusion); for
     │             comparisons, each paper is searched separately
     └─▶ read      every passage of the paper in order (capped at ~16k tokens),
                   for "find all" questions and summaries
```

- **One planning call replaces the follow-up rewrite**, so an ordinary question still costs
  two LLM calls. If the planner's reply can't be parsed, it falls back to plain search.
- The plan is streamed to the Ask page ("Read the whole paper · Read all 4 passages of …"),
  stored on the trace (Insights), and each question type adds a task hint to the answer prompt
  (for "find all": *go through every source and list every match*).
- `ANSWER_MODE=classic` keeps the single-search path; evaluation runs compare the two.
- The question's language is now decided by its own rule: more Latin letters than Japanese
  characters means English, so "What does the report mean by 草原化?" is answered in English.

**Classic vs agent** (29 questions on three real documents; Gemini 3.5 Flash-Lite answers and
judges):

| | Classic | Agent |
|---|---|---|
| Correct, as judged | 93% | 98% |
| Correct, after correcting one judge error | 93% | 93% |
| Cited a relevant passage | 70% | **78%** |
| Answered in the question's language | 100% | 100% |
| Faithful / unanswerable → "not found" | 100% / 6 of 6 | 100% / 6 of 6 |
| Typical time | **6.4 s** | 9.1 s |

The agent cites better but is slower, and is no more correct once one judge error is fixed: an
answer saying "the sources do not state the total area" was graded correct. The first agent run
scored lower than classic (89%) because it reported "authors not recorded" for arXiv PDFs, which
have no author metadata; the document details now include the first page.

## Knowledge graph (GraphRAG, Neo4j)

**Build graph** on a paper (Library) runs a background job: passages go to the LLM in batches
of four, which returns entities (people, places, concepts, methods, measurements, …) and
relations between them. Two rules keep the graph trustworthy:

- **Every relation quotes its evidence**, checked to appear exactly in its passage; otherwise
  it is dropped (and logged with the reason).
- **Names are canonical English with the Japanese form as an alias**, so 鳥取砂丘 and
  "Tottori Sand Dunes" are one node. A paper's defined abbreviation is its single name ("DPR").

```
(:Paper)<-[:PART_OF]-(:Chunk)<-[:MENTIONED_IN]-(:Entity)-[:RELATED {relation, evidence}]->(:Entity)
```

The agent gets a **relations** route for "who did what for whom", "how is X related to Y" and
"what do these papers share": it finds the named entities (in English or Japanese), collects
their relations, the shortest path between two of them, or the entities shared by papers, and
answers from those facts **plus the passages they came from**, so citations point to real
text. Queries are fixed Cypher templates with parameters (no LLM-written Cypher). If the graph
has nothing, it falls back to search.

On the three test documents the graph has 120 entities and 97 relations, and connects the RAG
and DPR papers through what they share: DPR itself, Wikipedia, Natural Questions, TriviaQA,
BM25 and FAISS, among others.

The **Graph** tab shows the network (d3-force; size = connections; three colour families so
the colours stay distinguishable for colour-blind readers; a table view lists every relation
with its evidence). Clicking an entity shows its relations and opens its passages in the PDF.
Tests use a separate `test` namespace in Neo4j and never touch the app's graph.

Known limit: extraction can make plausible inferences that a passage does not state. Evidence
quotes make such cases checkable.

## Evaluation (Evaluation tab, admins)

Settings are changed on measurements, not guesses.

- **Test set:** questions with an expected answer and an *evidence quote* from the paper. A
  passage counts as relevant if it comes from that paper and contains the quote, which keeps
  working after re-chunking. Questions can be generated by the LLM (each generated quote is
  checked to really appear in the passage) or added by hand. The set also includes questions
  the library does **not** answer, where Shiori should say "not found".
- **Search runs (free, no LLM):** hit@1/3/5, MRR, **coverage** (the right passage passed the
  relevance cutoff) and **rejection** (unanswerable questions kept nothing).
- **Cutoff sweep:** every run stores all reranker scores, so coverage and rejection are
  recomputed for ten cutoffs without re-running. The recommendation is the strictest cutoff
  with the best balance.
- **Answer runs:** the full pipeline answers each question and an LLM judge grades
  **correctness** against the expected answer and **faithfulness** (every claim backed by the
  sources). The run records whether the answer cited a relevant passage.
- **Compare** any runs side by side. Each run stores its configuration (search mode,
  reranker, cutoff, models, prompt version), so results stay traceable.

Runs execute in the background in the API process; a run cut off by a restart is marked
failed on the next start.

### Results on real papers

Three documents (108 passages), 29 hand-written questions, 6 of them unanswerable. Full tables
and the decisions they changed are in [docs/RESULTS.md](docs/RESULTS.md).

| Run | Hit@5 | Coverage | Correct | Faithful | Unanswerable → "not found" | Right language | Time |
|---|---|---|---|---|---|---|---|
| Search: hybrid, no reranker | 52% | 57% | | | | | 0.16 s |
| Search: hybrid + reranker, bare passage text | 61% | 61% | | | | | 5.4 s |
| **Search: hybrid + reranker, with title and section** | **74%** | **74%** | | | | | 4.9 s |
| Answers, classic single search | | 74% | 93% | 100% | 6 / 6 | 100% | 6.4 s |
| Answers, agent | | 78% | 98% (93% after one judge correction) | 100% | 6 / 6 | 100% | 9.1 s |

Search scores are strict (the one passage holding the exact evidence quote must be found);
answers can use any passage, so they score higher. The set is small and one question is worth
about 4 points. Add real questions from your lab before drawing finer conclusions.

## Accounts and security

- **First run:** open the app and create the admin account. Papers and conversations that
  existed before accounts are assigned to that admin.
- **Roles:** admins add lab members (Users tab), change roles, reset passwords and deactivate
  accounts. Members upload, search and ask. The paper library is shared by the lab; only the
  uploader or an admin can re-process or delete a paper. Conversations are private.
- **Sessions:** passwords are hashed with Argon2id. The session is a signed token (JWT, HS256,
  `SECRET_KEY` in `.env`) in an **httpOnly, SameSite=Lax** cookie, so page scripts cannot read
  it and other sites cannot send it with POST requests. Each token carries a session version;
  changing a password or deactivating a user invalidates all their sessions immediately.
- **Login protection:** the same error for unknown email and wrong password, constant-time
  checks, and 5 failed attempts per email and address per 15 minutes before a cooldown.
- Every API except `/api/health` and login requires a session. Set `COOKIE_SECURE=true` when
  serving over HTTPS.

## Deploy (Docker Compose)

Everything runs in containers; only the web server (Caddy) is exposed, and it obtains HTTPS
certificates automatically when given a domain name.

```bash
cp .env.production.example .env.production   # set SITE_ADDRESS and every secret
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

- **Images**: one backend image runs the API (it applies database migrations at start) and
  the worker; the web image is the built frontend served by Caddy, which proxies `/api`
  (answer streaming kept intact) and adds security headers.
- **Secrets are required**: the stack refuses to start without `POSTGRES_PASSWORD`,
  `NEO4J_PASSWORD` and `SECRET_KEY`. Databases are not published on any port.
- **First start** downloads the search models (~4.5 GB) into the `models` volume; later
  starts reuse them. A machine with 8 GB RAM or more is recommended (no GPU needed).
- **HTTPS**: set `SITE_ADDRESS=shiori.your-lab.example` (ports 80/443 reachable) and
  `COOKIE_SECURE=true`. On a private network, `SITE_ADDRESS=:80` with `COOKIE_SECURE=false`.
- **Updates**: `git pull` then the same `up -d --build`. Backups: the `pgdata`, `uploads` and
  `neo4jdata` volumes (the graph can also be rebuilt from the Library).

## Tests and linting

```bash
cd backend && uv run pytest && uv run ruff check .
cd frontend && npm run lint && npm test && npm run build
```

## Roadmap

- [x] **Phase 0**: Setup: repo, Docker, FastAPI, React, CI
- [x] **Phase 1**: Ingestion: PDF parsing, chunking by heading, bge-m3 embeddings, background jobs
- [x] **Phase 2**: Retrieval: hybrid search (pgvector + keyword index, merged with RRF) + reranker
- [x] **Phase 3**: Answers with citations: streaming, chat history, JP ⇄ EN
- [x] **Phase 4**: Frontend: library, chat, PDF viewer with citation highlights, accounts and roles
- [x] **Phase 5**: Observability: per-query traces (retrieval, prompt, tokens, cost, latency)
- [x] **Phase 6**: Evaluation dashboard: golden set, recall@k, MRR, faithfulness, experiments
- [x] **Phase 7**: Agentic routing (LangGraph): question planning, multi-query search, whole-paper reading
- [x] **Phase 8**: GraphRAG: entity graph in Neo4j, graph questions, graph view
- [x] **Phase 9**: Deploy (Docker Compose + Caddy HTTPS), results write-up ([docs/RESULTS.md](docs/RESULTS.md))
