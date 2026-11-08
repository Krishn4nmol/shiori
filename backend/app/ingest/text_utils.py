"""Language-aware text helpers that work for both English and Japanese."""

import re

# Hiragana, Katakana, CJK ideographs, and full-width forms
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿＀-￯]")
_LATIN_WORD = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")
# Split after sentence-ending punctuation. Japanese sentences need no trailing space.
_SENTENCE_END = re.compile(r"(?<=[。！？])|(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def cjk_ratio(text: str) -> float:
    """Share of non-space characters that are Japanese/Chinese."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if _CJK.match(c)) / len(chars)


def detect_language(text: str) -> str:
    """'ja' or 'en' for passages. Good enough for routing; bge-m3 itself is multilingual."""
    return "ja" if cjk_ratio(text) > 0.2 else "en"


def question_language(text: str) -> str:
    """The language a question is asked in, which the answer should use.

    English questions often quote Japanese terms ("What does 植生被覆率 mean here?"), so
    the passage rule would call them Japanese. A question is English when it has more Latin
    letters than Japanese characters.
    """
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    cjk = len(_CJK.findall(text))
    return "en" if latin > cjk else "ja"


def estimate_tokens(text: str) -> int:
    """Rough token count without loading a tokenizer.

    Japanese: about 1 token per character. English: about 1.3 tokens per word.
    """
    cjk = len(_CJK.findall(text))
    words = len(_LATIN_WORD.findall(text))
    return cjk + round(words * 1.3)


def split_sentences(text: str) -> list[str]:
    parts = (p.strip() for p in _SENTENCE_END.split(text))
    return [p for p in parts if p]


def normalize(text: str) -> str:
    """For comparing headings and repeated headers: lowercase, digits and spaces removed."""
    return re.sub(r"[\s\d]+", "", text).lower()
