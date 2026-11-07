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
    for i, block in enumerate(blocks):
        if (
            block.kind == "heading"
            and block.page == 1
            and normalize(block.text) == normalize(title or "")
        ):
            del blocks[i]
            break
    return ParsedDocument(
        title=title,
        authors=(meta.get("author") or "").strip() or None,
        page_count=page_count,
        blocks=blocks,
    )


def _page_lines(page: pymupdf.Page, page_no: int) -> list[_Line]:
    lines: list[_Line] = []
    data = page.get_text("dict", sort=True)
    for block_no, block in enumerate(data["blocks"]):
        if block.get("type") != 0:  # 0 = text, 1 = image
            continue
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans).strip()
            size = max(s["size"] for s in spans)
            bold = all(s["flags"] & 16 or "Bold" in s["font"] for s in spans)
            lines.append(_Line(text, round(size, 1), bold, page_no, block_no))
    return lines


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
    return biggest.text if biggest.size > body_size else None
