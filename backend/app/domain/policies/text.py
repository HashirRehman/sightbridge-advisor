import re
import unicodedata

# Curly quotes and en/em dashes → ASCII (by code point to keep the source unambiguous).
_QUOTE_CHARS = str.maketrans(
    {chr(0x2018): "'", chr(0x2019): "'", chr(0x201C): '"', chr(0x201D): '"'}
    | {chr(0x2013): "-", chr(0x2014): "-"}
)
_NON_WORD = re.compile(r"[^a-z0-9$%§.\-' ]+")
_SPACES = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    "a an and any are as at be been but by can could do does for from had has have how i if in "
    "into is it its may might must no not of on or other such than that the their them then "
    "there these they this those to under was were what when where which while who will with "
    "would you your".split()
)


def normalize(text: str) -> str:
    """Lowercase, unify quote/dash variants and collapse whitespace and punctuation runs.
    Used for tolerant-but-exact quote matching against passage text."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTE_CHARS).lower()
    text = _NON_WORD.sub(" ", text)
    # "tax -deferred" / "tax - deferred" (PDF artefacts) ≡ "tax-deferred"
    text = re.sub(r"\s*-\s*", "-", text)
    return _SPACES.sub(" ", text).strip()


def content_words(text: str) -> set[str]:
    """Crudely stemmed content words, for lexical overlap checks."""
    words = set()
    for w in _WORD.findall(unicodedata.normalize("NFKC", text).lower()):
        if w in STOPWORDS or len(w) < 3:
            continue
        for suffix in ("ing", "ies", "es", "ed", "s"):
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        words.add(w)
    return words
