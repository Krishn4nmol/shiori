import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.search import get_reranker_if_enabled
from app.db import SessionLocal
from app.ingest.embedder import get_embedder
from app.main import app
from app.models import Document
from app.retrieval.search import SearchFilters, rrf_fuse, search
from app.retrieval.tokenize import search_tokens, tsquery
from tests.fakes import FixedReranker, ReverseReranker, TokenEmbedder

# --- tokenizer and fusion (no database) -------------------------------------------------


def test_japanese_is_split_into_bigrams_and_kanji() -> None:
    tokens = search_tokens("鳥取砂丘を測定した")
    assert {"鳥取", "砂丘", "測定", "鳥", "丘"} <= set(tokens)
    assert "した" not in tokens  # hiragana-only grammar is dropped


def test_english_stopwords_dropped_and_case_folded() -> None:
    assert search_tokens("What did THE Methods measure?") == ["methods", "measure"]


def test_full_width_text_is_normalized() -> None:
    assert search_tokens("ＡＢＣ　１２３") == ["abc", "123"]


def test_tsquery_or_joins_tokens_and_handles_empty() -> None:
    assert tsquery("sand dunes") == "'sand' | 'dunes'"
    assert tsquery("the of and ?") is None


def test_rrf_rewards_agreement_between_lists() -> None:
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fused = rrf_fuse([[(a, 0.9), (b, 0.8)], [(b, 3.0), (c, 2.0)]])
    assert [cid for cid, _ in fused] == [b, a, c]  # b is in both lists
    assert fused[0][1] == pytest.approx(1 / 62 + 1 / 61)


# --- search against the database --------------------------------------------------------


async def _search(query: str, **kwargs):
    async with SessionLocal() as session:
        return await search(session, query, embedder=TokenEmbedder(), **kwargs)


async def test_hybrid_search_finds_the_right_paper(library: dict[str, uuid.UUID]) -> None:
    result = await _search("creatinine blood pressure hospital", reranker=None)
    assert result.hits[0].document_id == library["kidney"]
    top = result.hits[0]
    assert top.vector_rank == 1 and top.keyword_rank == 1
    assert {"embed", "vector", "keyword", "fuse", "total"} <= set(result.timings_ms)


async def test_japanese_query_finds_japanese_section(library: dict[str, uuid.UUID]) -> None:
    result = await _search("鳥取砂丘で何を測定しましたか", reranker=None, mode="keyword")
    assert result.hits, "keyword search should match the Japanese bigrams"
    assert result.hits[0].language == "ja"
    assert result.hits[0].page_start == 2


async def test_vector_only_mode_skips_keyword_search(library: dict[str, uuid.UUID]) -> None:
    result = await _search("vegetation cover grain size", reranker=None, mode="vector")
    assert "keyword" not in result.timings_ms
    assert all(h.keyword_rank is None for h in result.hits)


async def test_filters_by_document_and_language(library: dict[str, uuid.UUID]) -> None:
    only_dunes = SearchFilters(document_ids=[library["dunes"]])
    result = await _search("hospital visit", reranker=None, filters=only_dunes)
    assert all(h.document_id == library["dunes"] for h in result.hits)

    only_ja = SearchFilters(language="ja")
    result = await _search("measured sites", reranker=None, filters=only_ja)
    assert result.hits and all(h.language == "ja" for h in result.hits)


async def test_documents_not_ready_are_hidden(library: dict[str, uuid.UUID]) -> None:
    async with SessionLocal() as session:
        doc = await session.get(Document, library["kidney"])
        doc.status = "processing"
        await session.commit()
    result = await _search("creatinine blood pressure", reranker=None)
    assert all(h.document_id != library["kidney"] for h in result.hits)


async def test_reranker_reorders_only_the_top_candidates(library: dict[str, uuid.UUID]) -> None:
    plain = await _search("sand dunes vegetation", reranker=None, k=50)
    reranker = ReverseReranker()
    reranked = await _search("sand dunes vegetation", reranker=reranker, k=50, rerank_candidates=3)

    assert reranker.calls == [3]
    assert len(reranked.hits) == 3
    assert [h.chunk_id for h in reranked.hits] == [h.chunk_id for h in plain.hits[:3]][::-1]
    assert "rerank" in reranked.timings_ms


async def test_query_with_no_keywords_still_uses_vectors(library: dict[str, uuid.UUID]) -> None:
    result = await _search("what is the", reranker=None)
    assert all(h.keyword_rank is None for h in result.hits)


async def test_cutoff_hides_irrelevant_results(library: dict[str, uuid.UUID]) -> None:
    result = await _search("sand dunes", reranker=FixedReranker(), min_rerank_score=0.01)
    assert [h.rerank_score for h in result.hits] == [0.6]
    assert result.filtered_out > 0
    assert result.best_rerank_score == 0.6


async def test_cutoff_can_hide_everything(library: dict[str, uuid.UUID]) -> None:
    result = await _search("who created it", reranker=ReverseReranker(), min_rerank_score=0.99)
    assert result.hits == []
    assert result.filtered_out == min(result.candidates, 20)  # every reranked result
    assert result.best_rerank_score is not None and result.best_rerank_score < 0.99


async def test_no_cutoff_without_reranker(library: dict[str, uuid.UUID]) -> None:
    result = await _search("sand dunes", reranker=None, min_rerank_score=0.5)
    assert result.hits and result.filtered_out == 0


# --- API --------------------------------------------------------------------------------


@pytest.fixture
async def client(library: dict[str, uuid.UUID]) -> AsyncIterator[AsyncClient]:
    app.dependency_overrides[get_embedder] = TokenEmbedder
    app.dependency_overrides[get_reranker_if_enabled] = ReverseReranker
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_search_endpoint(client: AsyncClient, library: dict[str, uuid.UUID]) -> None:
    res = await client.post("/api/search", json={"query": "creatinine", "k": 3})
    assert res.status_code == 200
    body = res.json()
    assert body["reranked"] is True
    assert 0 < len(body["hits"]) <= 3
    hit = body["hits"][0]
    assert {"document_title", "section", "page_start", "rrf_score", "rerank_score"} <= set(hit)
    assert body["timings_ms"]["total"] > 0


async def test_search_endpoint_can_skip_reranking(client: AsyncClient) -> None:
    res = await client.post("/api/search", json={"query": "dunes", "rerank": False})
    body = res.json()
    assert body["reranked"] is False
    assert all(h["rerank_score"] is None for h in body["hits"])


async def test_search_endpoint_applies_cutoff(
    client: AsyncClient, library: dict[str, uuid.UUID]
) -> None:
    app.dependency_overrides[get_reranker_if_enabled] = FixedReranker
    body = (await client.post("/api/search", json={"query": "dunes"})).json()
    assert body["min_score"] == 0.01
    assert len(body["hits"]) == 1 and body["filtered_out"] > 0

    everything = (await client.post("/api/search", json={"query": "dunes", "min_score": 0})).json()
    assert len(everything["hits"]) > 1 and everything["filtered_out"] == 0


async def test_search_endpoint_validates_input(client: AsyncClient) -> None:
    assert (await client.post("/api/search", json={"query": ""})).status_code == 422
    assert (
        await client.post("/api/search", json={"query": "x", "mode": "magic"})
    ).status_code == 422
