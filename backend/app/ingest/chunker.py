"""Split parsed blocks into search-sized chunks that respect section boundaries.

- A chunk never spans two sections, so every chunk has one clear heading path.
- Paragraphs are packed up to a target size; long paragraphs are split by sentence.
- Neighbouring chunks in a section overlap by a few sentences so no fact is cut in half.
- Reference lists are skipped: they match many queries but answer none.
"""

from dataclasses import dataclass

from app.ingest.pdf_parser import Block
from app.ingest.text_utils import detect_language, estimate_tokens, normalize, split_sentences

SKIP_SECTIONS = {"references", "bibliography", "参考文献", "引用文献"}
MIN_CHUNK_TOKENS = 40


@dataclass
class ChunkDraft:
    text: str
    section: str | None
    page_start: int
    page_end: int
    language: str
    token_count: int


@dataclass
class _Piece:
    text: str
    page: int
    tokens: int


def chunk_blocks(
    blocks: list[Block], *, target_tokens: int = 350, overlap_tokens: int = 50
) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    for section, pieces in _sections(blocks, target_tokens):
        chunks.extend(_pack(pieces, section, target_tokens, overlap_tokens))
    return chunks


def _sections(blocks: list[Block], target_tokens: int) -> list[tuple[str | None, list[_Piece]]]:
    """Group text into (heading path, pieces), splitting oversized paragraphs by sentence."""
    stack: list[tuple[int, str]] = []  # (level, heading)
    sections: list[tuple[str | None, list[_Piece]]] = [(None, [])]
    skipping = False

    for block in blocks:
        if block.kind == "heading":
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, block.text))
            stripped = normalize(block.text.lstrip("0123456789.IVX "))
            skipping = stripped in {normalize(s) for s in SKIP_SECTIONS}
            sections.append((" > ".join(h for _, h in stack), []))
            continue
        if skipping:
            continue
        pieces = sections[-1][1]
        tokens = estimate_tokens(block.text)
        if tokens <= target_tokens:
            pieces.append(_Piece(block.text, block.page, tokens))
        else:
            pieces.extend(
                _Piece(s, block.page, estimate_tokens(s)) for s in split_sentences(block.text)
            )
    return [(name, pieces) for name, pieces in sections if pieces]


def _pack(pieces: list[_Piece], section: str | None, target: int, overlap: int) -> list[ChunkDraft]:
    chunks: list[list[_Piece]] = []
    current: list[_Piece] = []
    carried = 0  # how many pieces at the start of `current` are overlap from the last chunk
    size = 0

    for piece in pieces:
        if len(current) > carried and size + piece.tokens > target:
            chunks.append(current)
            current = _tail(current, overlap)
            carried = len(current)
            size = sum(p.tokens for p in current)
        current.append(piece)
        size += piece.tokens

    fresh = current[carried:]
    if fresh:
        # A tiny leftover reads better merged into the previous chunk than on its own.
        if chunks and sum(p.tokens for p in fresh) < MIN_CHUNK_TOKENS:
            chunks[-1].extend(fresh)
        else:
            chunks.append(current)

    return [_draft(c, section) for c in chunks]


def _tail(pieces: list[_Piece], overlap: int) -> list[_Piece]:
    """Last sentences of a chunk, up to `overlap` tokens, to start the next chunk with."""
    if overlap <= 0:
        return []
    sentences = [
        _Piece(s, p.page, estimate_tokens(s)) for p in pieces for s in split_sentences(p.text)
    ]
    tail: list[_Piece] = []
    size = 0
    for s in reversed(sentences):
        if size + s.tokens > overlap:
            break
        tail.insert(0, s)
        size += s.tokens
    return tail


def _draft(pieces: list[_Piece], section: str | None) -> ChunkDraft:
    # Japanese text has no spaces between sentences; English paragraphs keep line breaks.
    sep = "" if detect_language(pieces[0].text) == "ja" else "\n"
    text = sep.join(p.text for p in pieces)
    return ChunkDraft(
        text=text,
        section=section,
        page_start=min(p.page for p in pieces),
        page_end=max(p.page for p in pieces),
        language=detect_language(text),
        token_count=estimate_tokens(text),
    )
