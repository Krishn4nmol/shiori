"""Turn a PDF into an ordered list of headings and paragraphs, each with its page number.

Headings come from the PDF's outline (table of contents) when it has one, otherwise from
font size, bold text, and section numbering. Running headers/footers and page numbers are
removed so they do not pollute search results.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pymupdf

from app.ingest.text_utils import cjk_ratio, normalize

# "1", "2.3", "3.1.4", "IV.", "A.1" followed by a title
_NUMBERED = re.compile(r"^((?:\d+|[IVX]+|[A-Z])(?:\.\d+)*)\.?\s+\S")
# 第1章 / 第2節 / 1.2節
_JA_NUMBERED = re.compile(
    r"^(第[0-9０-９一二三四五六七八九十]+[章節項]|[0-9０-９]+(?:\.[0-9０-９]+)*[章節]?)\s*\S"
)
_KNOWN_HEADINGS = {
    "abstract", "introduction", "relatedwork", "background", "method", "methods",
    "methodology", "experiments", "results", "discussion", "conclusion", "conclusions",
    "acknowledgments", "acknowledgements", "references", "bibliography", "appendix",
    "概要", "要旨", "はじめに", "序論", "関連研究", "手法", "提案手法", "実験", "結果",
    "考察", "結論", "おわりに", "まとめ", "謝辞", "参考文献", "付録",
}  # fmt: skip


@dataclass
class Block:
    kind: Literal["heading", "text"]
    text: str
    page: int  # 1-based
    level: int = 0  # heading depth: 1 = top level


@dataclass
class ParsedDocument:
    title: str | None
    authors: str | None
    page_count: int
    blocks: list[Block] = field(default_factory=list)


@dataclass
class _Line:
    text: str
    size: float
    bold: bool
    page: int
    block_no: int


def parse_pdf(path: Path) -> ParsedDocument:
    with pymupdf.open(path) as pdf:
        pages = [_page_lines(page, i + 1) for i, page in enumerate(pdf)]
        toc = pdf.get_toc(simple=True)  # [[level, title, page], ...]
        meta = pdf.metadata or {}
        page_count = pdf.page_count

    pages = _strip_running_headers(pages)
    lines = [line for page in pages for line in page]
    body_size = _body_font_size(lines)
    toc_titles = {(normalize(title), page): level for level, title, page in toc if title.strip()}

    blocks = _group_blocks(lines, body_size, toc_titles)
    title = _pick_title(meta.get("title"), lines, body_size)
    # The paper title is stored on the document; as a heading it would prefix every
    # section path on page 1 ("Title > Abstract").
    # A wrapped title is several heading lines; drop the first run of them.
    norm_title = normalize(title or "")
    title_parts = [
        i
        for i, b in enumerate(blocks)
        if b.kind == "heading" and b.page == 1 and norm_title and normalize(b.text) in norm_title
    ]
    run = title_parts[:1]
    for i in title_parts[1:]:
        if i != run[-1] + 1:
            break
        run.append(i)
    for i in reversed(run):
        del blocks[i]
    return ParsedDocument(
        title=title,
        authors=(meta.get("author") or "").strip() or None,
        page_count=page_count,
        blocks=blocks,
    )


# Control characters (NUL in math-heavy PDFs, for one) that Postgres or the UI can't store.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffe\uffff]")


# Typographic ligatures (one character for "fi" in "verification") would break keyword search.
_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\ufb06": "st",
    }
)


def clean_text(text: str) -> str:
    return _CONTROL.sub("", text).translate(_LIGATURES)


def _reading_order(blocks: list[dict], page_width: float) -> list[dict]:
    """Order blocks for reading, column by column on two-column pages.

    Full-width blocks (title, wide figures) split the page into bands; inside each band the
    left column is read before the right one.
    """
    mid = page_width / 2
    blocks = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    left = [b for b in blocks if b["bbox"][2] <= mid + 10]
    right = [b for b in blocks if b["bbox"][0] >= mid - 10]
    if len(left) < 3 or len(right) < 3:  # not a two-column page
        return blocks
    ordered: list[dict] = []
    band: list[dict] = []
    for b in blocks:
        if b in left or b in right:
            band.append(b)
            continue
        ordered += sorted(band, key=lambda x: (x["bbox"][0] >= mid - 10, x["bbox"][1]))
        band = []
        ordered.append(b)
    ordered += sorted(band, key=lambda x: (x["bbox"][0] >= mid - 10, x["bbox"][1]))
    return ordered


def _page_lines(page: pymupdf.Page, page_no: int) -> list[_Line]:
    lines: list[_Line] = []
    data = page.get_text("dict", sort=True)
    text_blocks = [b for b in data["blocks"] if b.get("type") == 0]  # 0 = text, 1 = image
    for block_no, block in enumerate(_reading_order(text_blocks, page.rect.width)):
        for line in block["lines"]:
            # Rotated text (e.g. the vertical arXiv stamp in the margin) is never body text.
            dx, dy = line.get("dir", (1.0, 0.0))
            if abs(dy) > 0.1 or dx < 0:
                continue
            spans = [s for s in line["spans"] if clean_text(s["text"]).strip()]
            if not spans:
                continue
            text = clean_text(_join_spans(spans)).strip()
            size = max(s["size"] for s in spans)
            bold = all(s["flags"] & 16 or "Bold" in s["font"] for s in spans)
            line_ = _Line(text, round(size, 1), bold, page_no, block_no)
            prev = lines[-1] if lines else None
            # "2.1" and "Models" are often separate lines of one block: one heading.
            if prev and prev.block_no == block_no and _BARE_NUMBER.fullmatch(prev.text):
                prev.text = f"{prev.text} {text}"
                prev.bold = prev.bold and bold
                continue
            lines.append(line_)
    return lines


_BARE_NUMBER = re.compile(r"(?:\d+|[IVX]+|[A-Z])(?:\.\d+)*\.?")


def _join_spans(spans: list[dict]) -> str:
    """Join spans, adding the space a PDF often encodes as a gap rather than a character."""
    out = ""
    for prev, span in zip([None, *spans], spans, strict=False):
        text = span["text"]
        if prev is not None and out and not out.endswith(" ") and not text.startswith(" "):
            gap = span["bbox"][0] - prev["bbox"][2]
            if gap > span["size"] * 0.2 and not (cjk_ratio(out[-1:]) or cjk_ratio(text[:1])):
                out += " "
        out += text
    return out


def _strip_running_headers(pages: list[list[_Line]]) -> list[list[_Line]]:
    """Drop lines repeated at the top/bottom of many pages, and bare page numbers."""
    if len(pages) >= 3:
        edge_counts: Counter[str] = Counter()
        for lines in pages:
            edges = {normalize(line.text) for line in lines[:2] + lines[-2:]}
            edge_counts.update(e for e in edges if e)
        threshold = max(3, len(pages) // 2)
        repeated = {text for text, n in edge_counts.items() if n >= threshold}
    else:
        repeated = set()

    cleaned = []
    for lines in pages:
        edge_ids = {id(line) for line in lines[:2] + lines[-2:]}
        cleaned.append(
            [
                line
                for line in lines
                if not re.fullmatch(r"[-–—\s]*\d+[-–—\s]*|page \d+( of \d+)?", line.text.lower())
                and not (id(line) in edge_ids and normalize(line.text) in repeated)
            ]
        )
    return cleaned


def _body_font_size(lines: list[_Line]) -> float:
    """The most common font size, weighted by amount of text."""
    sizes: Counter[float] = Counter()
    for line in lines:
        sizes[line.size] += len(line.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _heading_level(line: _Line, body_size: float, toc: dict[tuple[str, int], int]) -> int:
    """0 if the line is body text, otherwise its heading level (1 = top)."""
    text = line.text
    if (level := toc.get((normalize(text), line.page))) is not None:
        return level

    if not re.search(r"[^\W\d_]", text):  # no letters: a number or value from a table
        return 0
    is_cjk = cjk_ratio(text) > 0.2
    too_long = len(text) > (40 if is_cjk else 120) or len(text.split()) > 15
    if too_long or text.endswith((".", ",", "、", "，", ":", ";")) and not _NUMBERED.match(text):
        return 0

    bigger = line.size >= body_size * 1.15
    numbered = _NUMBERED.match(text) or _JA_NUMBERED.match(text)
    known = normalize(re.sub(r"^[\dIVX.\s]+", "", text)) in _KNOWN_HEADINGS

    if not (bigger or (line.bold and (numbered or known)) or (known and len(text) < 30)):
        return 0
    if numbered and (m := _NUMBERED.match(text)):
        return m.group(1).count(".") + 1
    if known or line.size >= body_size * 1.5:  # "Abstract", "References" are top level
        return 1
    return 2 if bigger else 1


def _group_blocks(
    lines: list[_Line], body_size: float, toc: dict[tuple[str, int], int]
) -> list[Block]:
    """Merge consecutive body lines of the same PDF block into paragraphs."""
    blocks: list[Block] = []
    para: list[_Line] = []

    def flush() -> None:
        if para:
            blocks.append(Block("text", _join_lines([p.text for p in para]), para[0].page))
            para.clear()

    for line in lines:
        level = _heading_level(line, body_size, toc)
        if level:
            flush()
            blocks.append(Block("heading", line.text, line.page, level))
            continue
        if para and (line.block_no != para[-1].block_no or line.page != para[-1].page):
            flush()
        para.append(line)
    flush()
    return [b for b in blocks if b.text.strip()]


def _join_lines(lines: list[str]) -> str:
    """Join wrapped lines: no space between Japanese lines, fix English hyphenation."""
    out = ""
    for line in lines:
        if not out:
            out = line
        elif out.endswith("-") and line[:1].islower():
            out = out[:-1] + line
        elif cjk_ratio(out[-1:]) or cjk_ratio(line[:1]):
            out += line
        else:
            out += " " + line
    return re.sub(r"\s+", " ", out).strip()


def _pick_title(meta_title: str | None, lines: list[_Line], body_size: float) -> str | None:
    if meta_title and len(meta_title.strip()) > 3 and not meta_title.lower().endswith(".pdf"):
        return meta_title.strip()
    first_page = [line for line in lines if line.page == 1 and len(line.text) > 3]
    if not first_page:
        return None
    biggest = max(first_page, key=lambda line: line.size)
    if biggest.size <= body_size:
        return None
    # Long titles wrap: keep the following lines of the same block in the same font size.
    start = first_page.index(biggest)
    parts = [biggest.text]
    for line in first_page[start + 1 : start + 4]:
        if abs(line.size - biggest.size) > 0.5 or line.bold != biggest.bold:
            break
        parts.append(line.text)
    return _join_lines(parts)
