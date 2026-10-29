"""Turn original source bytes into located text sections (page for PDFs, heading for HTML)."""

import io
import re

from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag
from pypdf import PdfReader

from app.domain.models.documents import TextSection


class ExtractionError(Exception):
    pass


_DROP_TAGS = [
    "script", "style", "noscript", "nav", "header", "footer", "aside", "form",
    "button", "svg", "iframe", "figure", "template",
]  # fmt: skip
_BLOCK_TAGS = [
    "p", "div", "li", "ul", "ol", "dd", "dt", "dl", "tr", "table", "section",
    "article", "blockquote", "pre", "br", "h1", "h2", "h3", "h4", "h5", "h6",
]  # fmt: skip
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_HEADING_MARK = "\u0000H\u0000"
# Exact-match UI chrome that survives content selectors (share bars, newsletter prompts).
_BOILERPLATE_LINES = {
    "facebook", "x", "linkedin", "email", "print", "copy link", "share this article",
    "join the conversation", "follow us", "subscribe to our newsletter",
    "add us as a preferred source on google", "newsletter", "(image credit: getty images)",
}  # fmt: skip
_WS = re.compile("[ \t\u00a0\u2009\u200b]+")


def _clean_line(line: str) -> str:
    return _WS.sub(" ", line).strip()


def extract_html(html: bytes, selector: str) -> list[TextSection]:
    soup = BeautifulSoup(html, "html.parser")
    root = soup.select_one(selector)
    if root is None:
        raise ExtractionError(f"content selector {selector!r} matched nothing")

    for tag in root.find_all(_DROP_TAGS):
        tag.decompose()
    # Newlines around block elements only, so inline links/emphasis stay in their sentence.
    for tag in root.find_all(_BLOCK_TAGS):
        if not isinstance(tag, Tag):
            continue
        prefix = _HEADING_MARK if tag.name in _HEADING_TAGS else ""
        tag.insert_before(NavigableString("\n" + prefix))
        tag.insert_after(NavigableString("\n"))

    sections: list[TextSection] = []
    heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            sections.append(TextSection(text=text, section=heading))
        buffer.clear()

    for raw in root.get_text("").split("\n"):
        is_heading = raw.startswith(_HEADING_MARK)
        line = _clean_line(raw.replace(_HEADING_MARK, ""))
        if not line or line.lower() in _BOILERPLATE_LINES:
            continue
        if is_heading:
            flush()
            heading = line[:200]
        else:
            buffer.append(line)
    flush()

    if not sections:
        raise ExtractionError("no text extracted")
    return sections


# PDF text often splits hyphenated compounds across a stray space ("tax -deferred").
_PDF_HYPHEN = re.compile(r"(\w) -(\w)")


def extract_pdf(data: bytes) -> list[TextSection]:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise ExtractionError(f"unreadable PDF: {exc}") from exc

    sections: list[TextSection] = []
    for number, page in enumerate(reader.pages, start=1):
        lines = [_clean_line(line) for line in (page.extract_text() or "").splitlines()]
        text = "\n".join(line for line in lines if line)
        text = _PDF_HYPHEN.sub(r"\1-\2", text)
        if text:
            sections.append(TextSection(text=text, page_number=number))
    if not sections:
        raise ExtractionError("PDF has no extractable text (scanned?)")
    return sections
