"""
query_parser.py — NLP query → structured filter dict.

"Sarah in red hat at beach"
→ {
    "persons": ["Sarah"],
    "objects": ["hat"],
    "attributes": ["red"],
    "scenes": ["beach"],
    "date_range": None,
    "text_search": None,
  }

Uses spaCy en_core_web_sm (~12 MB) for NER + POS tagging.
Falls back to a pure-regex/word-list approach if spaCy is unavailable.

RAM: ~15 MB (spaCy sm model, loaded once).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ── Word lists (used both by regex fallback and to guide spaCy) ──────────────

SCENE_WORDS = {
    "beach", "sea", "ocean", "coast", "shore",
    "mountain", "mountains", "hill", "cliff",
    "city", "town", "downtown", "urban", "skyline",
    "forest", "jungle", "woods", "trees",
    "park", "garden",
    "indoor", "inside", "home", "house", "room",
    "office", "workplace",
    "street", "road", "highway",
    "restaurant", "cafe", "bar", "diner",
    "bedroom", "bathroom", "kitchen", "living room",
    "desert", "dunes",
    "snow", "winter", "glacier",
    "stadium", "arena",
    "airport", "station",
    "farm", "field",
}

COLOUR_WORDS = {
    "red", "orange", "yellow", "green", "cyan", "blue",
    "purple", "pink", "black", "white", "grey", "gray",
    "brown", "beige", "turquoise",
}

SIZE_WORDS = {"small", "large", "big", "tiny", "huge", "giant"}

# COCO object labels (subset most useful for photo search)
OBJECT_WORDS = {
    "person", "people", "dog", "cat", "bird", "horse", "cow", "sheep",
    "car", "truck", "bus", "bicycle", "motorcycle", "airplane", "boat",
    "train", "hat", "cap", "glasses", "sunglasses", "bag", "backpack",
    "handbag", "suitcase", "umbrella", "bottle", "cup", "wine glass",
    "bowl", "banana", "apple", "sandwich", "pizza", "cake", "donut",
    "chair", "couch", "sofa", "bed", "table", "laptop", "phone",
    "cell phone", "book", "clock", "vase", "flower",
}

# Preposition patterns that signal scene
_AT_RE = re.compile(
    r"\b(?:at|in|on|near|by)\s+(?:the\s+)?([a-z ]+?)(?:\s|$|,)",
    re.IGNORECASE,
)
_WEARING_RE = re.compile(
    r"\bwearing\s+(?:a\s+)?([a-z ]+?)(?:\s|$|,)",
    re.IGNORECASE,
)
_WITH_RE = re.compile(
    r"\bwith\s+(?:a\s+)?([a-z ]+?)(?:\s|$|,)",
    re.IGNORECASE,
)

_nlp = None
_NLP_TRIED = False


def _load_spacy():
    global _nlp, _NLP_TRIED
    if _NLP_TRIED:
        return _nlp
    _NLP_TRIED = True
    try:
        import spacy
        _nlp = spacy.load("en_core_web_sm")
    except Exception:
        _nlp = None
    return _nlp


@dataclass
class ParsedQuery:
    persons: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)   # colours + sizes
    scenes: list[str] = field(default_factory=list)
    date_range: Optional[tuple] = None    # (start, end) datetime | None
    text_search: Optional[str] = None     # raw OCR text term


def parse(query: str) -> ParsedQuery:
    """Parse a natural-language photo query into structured filters."""
    result = ParsedQuery()
    q = query.lower().strip()
    tokens = re.findall(r"[a-z]+", q)
    token_set = set(tokens)

    # ── spaCy path ───────────────────────────────────────────────────────────
    nlp = _load_spacy()
    if nlp is not None:
        doc = nlp(query)
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                result.persons.append(ent.text.strip())
            elif ent.label_ in ("GPE", "LOC", "FAC"):
                result.scenes.append(ent.text.lower().strip())
            elif ent.label_ == "DATE":
                result.date_range = _parse_date_ent(ent.text)

    # ── Word-list extraction (both paths) ────────────────────────────────────
    for word in token_set & COLOUR_WORDS:
        if word not in result.attributes:
            result.attributes.append(word)

    for word in token_set & SIZE_WORDS:
        if word not in result.attributes:
            result.attributes.append(word)

    for word in token_set & SCENE_WORDS:
        if word not in result.scenes:
            result.scenes.append(word)

    for word in token_set & OBJECT_WORDS:
        if word not in result.objects:
            result.objects.append(word)

    # ── Capitalised words not caught by NER → persons ────────────────────────
    if nlp is None:
        for token in re.findall(r"\b[A-Z][a-z]+\b", query):
            if (token.lower() not in SCENE_WORDS
                    and token.lower() not in OBJECT_WORDS
                    and token not in result.persons):
                result.persons.append(token)

    # ── Preposition patterns ─────────────────────────────────────────────────
    for m in _AT_RE.finditer(q):
        phrase = m.group(1).strip()
        words = phrase.split()
        for w in words:
            if w in SCENE_WORDS and w not in result.scenes:
                result.scenes.append(w)

    # ── "text:" prefix for explicit OCR search ───────────────────────────────
    text_m = re.search(r'\btext:\s*"?([^"]+)"?', q)
    if text_m:
        result.text_search = text_m.group(1).strip()

    return result


def _parse_date_ent(text: str):
    """Very light date parser — returns None for now (extend as needed)."""
    return None
