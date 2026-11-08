"""Builds small but realistic research-paper PDFs for tests."""

from pathlib import Path

import pymupdf

LOREM = (
    "Sand dunes form where wind carries sand across open ground and drops it behind "
    "obstacles. Coastal dunes such as those in Tottori change shape every season. "
    "We measured vegetation cover and grain size at forty sites over three years. "
    "Each site was visited monthly and photographed from a fixed point. "
)
JA_TEXT = (
    "鳥取砂丘は日本最大級の海岸砂丘であり、季節によって形状が大きく変化する。"
    "本研究では三年間にわたり四十地点で植生被覆率と粒径を測定した。"
    "各地点は毎月調査され、固定点から写真撮影を行った。"
)


def _write(page: pymupdf.Page, y: float, text: str, size: float, font: str = "helv") -> float:
    """Write wrapped text starting at y; return the y below it."""
    rect = pymupdf.Rect(72, y, page.rect.width - 72, page.rect.height - 60)
    unused = page.insert_textbox(rect, text, fontsize=size, fontname=font)
    used_height = rect.height - unused
    return y + used_height + size * 0.8


def make_paper(path: Path) -> Path:
    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    # Re-fetch pages by index: adding pages invalidates earlier Page objects.
    for number, page in enumerate(doc, start=1):
        page.insert_text((72, 40), "Journal of Arid Land Studies", fontsize=8)  # running header
        page.insert_text((page.rect.width / 2, page.rect.height - 30), str(number), fontsize=8)

    p1 = doc[0]
    # A vertical arXiv-style stamp in the margin, in a larger font than the title: real arXiv
    # PDFs have one, and it must never become the title or body text.
    p1.insert_text((30, 600), "arXiv:2611.01234v2  [cs.CL]  7 Nov 2026", fontsize=24, rotate=90)
    y = _write(p1, 70, "Seasonal Change of Coastal Dunes", 20, "hebo")
    y = _write(p1, y, "Abstract", 12, "hebo")
    y = _write(p1, y, LOREM * 2, 10)
    y = _write(p1, y, "1 Introduction", 14, "hebo")
    y = _write(p1, y, LOREM * 3, 10)
    y = _write(p1, y, "1.1 Study area", 12, "hebo")
    _write(p1, y, LOREM * 2, 10)

    p2 = doc[1]
    y = _write(p2, 70, "2 Methods", 14, "hebo")
    y = _write(p2, y, LOREM * 6, 10)
    y = _write(p2, y, "3 鳥取砂丘の調査", 14, "japan")
    _write(p2, y, JA_TEXT * 3, 10, "japan")

    p3 = doc[2]
    y = _write(p3, 70, "4 Conclusion", 14, "hebo")
    y = _write(p3, y, LOREM, 10)
    y = _write(p3, y, "References", 14, "hebo")
    _write(p3, y, "[1] Tanaka, K. (2020). Dune dynamics. Arid Land Research 12, 1-20.", 10)

    doc.set_metadata({"title": "", "author": "A. Researcher"})
    doc.save(path)
    doc.close()
    return path
