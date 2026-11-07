"""Keyword tokens for English and Japanese, stored in a Postgres full-text index.

Japanese has no spaces between words, so it is indexed as overlapping 2-character pieces
("鳥取砂丘" -> 鳥取, 取砂, 砂丘), plus single kanji (犬, 花 are whole words). This is the
same idea as Lucene's CJK analyzer and PGroonga's default tokenizer, without needing a
custom Postgres build. Pieces made only of hiragana (して, ます, です) are grammar, not
content, so they are dropped. That also filters most furigana noise from PDFs.
"""

import re
import unicodedata

_LATIN = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿]+")
_HIRAGANA_ONLY = re.compile(r"^[぀-ゟ]+$")
_KANJI = re.compile(r"[㐀-䶿一-鿿]")

STOPWORDS = frozenset(
    "a an and are as at be but by can did do does for from had has have how i if in into is "
    "it its of on or our so than that the their them then there these they this to was we "
    "were what when where which who why will with you your".split()
)


def search_tokens(text: str) -> list[str]:
    """Distinct tokens in first-seen order."""
    text = unicodedata.normalize("NFKC", text).lower()
    tokens: dict[str, None] = {}

    for word in _LATIN.findall(text):
        if word not in STOPWORDS and (len(word) > 1 or word.isdigit()):
            tokens[word] = None

    for run in _CJK_RUN.findall(text):
        for char in run:
            if _KANJI.match(char):
                tokens[char] = None
        for i in range(len(run) - 1):
            pair = run[i : i + 2]
            if not _HIRAGANA_ONLY.match(pair):
                tokens[pair] = None

    return list(tokens)


def search_text(text: str) -> str:
    """Space-separated tokens, as stored in chunks.search_text."""
    return " ".join(search_tokens(text))


def tsquery(text: str) -> str | None:
    """An OR query over the tokens, in raw tsquery syntax (no Postgres parsing)."""
    tokens = search_tokens(text)
    return " | ".join(f"'{t}'" for t in tokens) if tokens else None
