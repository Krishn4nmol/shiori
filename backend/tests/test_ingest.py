from pathlib import Path

import pytest

from app.ingest.chunker import chunk_blocks
from app.ingest.pdf_parser import Block, parse_pdf
from app.ingest.text_utils import (
    detect_language,
    estimate_tokens,
    question_language,
    split_sentences,
)
from tests.pdf_factory import make_paper


@pytest.fixture(scope="module")
def paper(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_paper(tmp_path_factory.mktemp("pdfs") / "paper.pdf")


# --- text utils -------------------------------------------------------------------------


def test_detect_language() -> None:
    assert detect_language("Sand dunes change every season.") == "en"
    assert detect_language("鳥取砂丘は季節によって変化する。") == "ja"


def test_question_language_handles_quoted_japanese() -> None:
    assert question_language("What does the report mean by 草原化 (grassland conversion)?") == "en"
    assert question_language("鳥取砂丘で何を測定しましたか？") == "ja"
    assert question_language("「鳥取砂丘」の調査地点は何か所ですか") == "ja"


def test_estimate_tokens_counts_japanese_characters() -> None:
    assert estimate_tokens("鳥取砂丘") == 4
    assert estimate_tokens("ten words " * 5) == 13  # 10 words * 1.3


def test_split_sentences_handles_both_languages() -> None:
    assert split_sentences("First one. Second one! Third?") == [
        "First one.",
        "Second one!",
        "Third?",
    ]
    assert split_sentences("一文目。二文目。") == ["一文目。", "二文目。"]


# --- PDF parser -------------------------------------------------------------------------


def test_parser_finds_title_and_metadata(paper: Path) -> None:
    parsed = parse_pdf(paper)
    assert parsed.title == "Seasonal Change of Coastal Dunes"
    assert parsed.authors == "A. Researcher"
    assert parsed.page_count == 3


def test_parser_detects_headings_with_levels(paper: Path) -> None:
    headings = [(b.text, b.level) for b in parse_pdf(paper).blocks if b.kind == "heading"]
    texts = [t for t, _ in headings]
    for expected in ["Abstract", "1 Introduction", "1.1 Study area", "2 Methods", "References"]:
        assert expected in texts
    levels = dict(headings)
    assert levels["1 Introduction"] == 1
    assert levels["1.1 Study area"] == 2
    assert levels["Abstract"] == levels["References"] == 1
    # The paper title is document metadata, not a section heading.
    assert "Seasonal Change of Coastal Dunes" not in texts
    assert any("鳥取砂丘の調査" in t for t in texts)


def test_parser_removes_running_headers_and_page_numbers(paper: Path) -> None:
    all_text = " ".join(b.text for b in parse_pdf(paper).blocks)
    assert "Journal of Arid Land Studies" not in all_text
    assert not any(b.text.strip() in {"1", "2", "3"} for b in parse_pdf(paper).blocks)


def test_parser_joins_japanese_lines_without_spaces(paper: Path) -> None:
    ja = [b for b in parse_pdf(paper).blocks if b.kind == "text" and "砂丘" in b.text]
    assert ja
    assert "鳥取砂丘は日本最大級の海岸砂丘であり" in ja[0].text


# --- chunker ----------------------------------------------------------------------------


def test_chunks_keep_section_paths_and_pages(paper: Path) -> None:
    chunks = chunk_blocks(parse_pdf(paper).blocks, target_tokens=120, overlap_tokens=20)
    sections = {c.section for c in chunks}
    assert "Abstract" in sections
    assert "1 Introduction > 1.1 Study area" in sections
    methods = [c for c in chunks if c.section == "2 Methods"]
    assert len(methods) >= 2  # long section split into several chunks
    assert all(c.page_start == 2 for c in methods)


def test_chunks_skip_references(paper: Path) -> None:
    chunks = chunk_blocks(parse_pdf(paper).blocks)
    assert not any("Tanaka" in c.text for c in chunks)


def test_japanese_section_becomes_japanese_chunks(paper: Path) -> None:
    chunks = chunk_blocks(parse_pdf(paper).blocks)
    ja = [c for c in chunks if c.language == "ja"]
    assert ja and all("鳥取砂丘の調査" in (c.section or "") for c in ja)


def test_chunk_size_and_overlap() -> None:
    sentences = [f"Sentence number {i} talks about dunes and grass." for i in range(60)]
    blocks = [Block("heading", "2 Methods", 1, 1), Block("text", " ".join(sentences), 1)]
    chunks = chunk_blocks(blocks, target_tokens=100, overlap_tokens=25)

    assert len(chunks) > 3
    assert all(c.token_count <= 100 + 25 for c in chunks)
    # The last sentence of each chunk is repeated at the start of the next one.
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert split_sentences(a.text)[-1] in b.text
    # No text lost: every sentence appears in some chunk.
    joined = " ".join(c.text for c in chunks)
    assert all(s in joined for s in sentences)


def test_tiny_leftover_is_merged() -> None:
    blocks = [
        Block("text", "word " * 90, 1),
        Block("text", "Short tail.", 2),
    ]
    chunks = chunk_blocks(blocks, target_tokens=100, overlap_tokens=0)
    assert len(chunks) == 1
    assert chunks[0].page_end == 2
