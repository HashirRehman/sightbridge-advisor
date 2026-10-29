import io
import json
import math

import pytest
from pydantic import ValidationError

from app.application.ingestion.chunking import CHUNK_SIZE, chunk_sections
from app.application.ingestion.extraction import ExtractionError, extract_html, extract_pdf
from app.application.ingestion.manifest import load_manifest
from app.core.config import Settings
from app.domain.models.documents import TextSection
from app.infrastructure.bedrock.embeddings import BedrockTitanEmbedder
from app.infrastructure.retrieval.local_embedder import LocalHashingEmbedder


def _minimal_pdf(pages: list[str]) -> bytes:
    """Build a tiny valid PDF with one text line per page (no PDF library needed)."""
    # Objects: 1 catalog, 2 pages, 3 font, then (page, content stream) pairs from 4.
    kids = [f"{4 + i * 2} 0 R" for i in range(len(pages))]
    body = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for text in pages:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        body.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {len(body) + 2} 0 R >>"
        )
        body.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    out = io.BytesIO(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(body, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n{obj}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(body) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return out.getvalue()


def test_html_extraction_keeps_headings_and_inline_text_drops_chrome() -> None:
    html = b"""<html><body><nav>Menu</nav><main>
      <h2>Deferred exchanges</h2>
      <p>Identify property within <a href="#">45 days</a> of the transfer.</p>
      <p>Share this article</p>
      <h2>Basis</h2><p>Basis carries over.</p>
      <footer>Footer</footer></main></body></html>"""

    sections = extract_html(html, "main")

    assert sections == [
        TextSection(
            text="Identify property within 45 days of the transfer.", section="Deferred exchanges"
        ),
        TextSection(text="Basis carries over.", section="Basis"),
    ]


def test_html_extraction_fails_loudly_when_selector_misses() -> None:
    with pytest.raises(ExtractionError):
        extract_html(b"<html><body><p>x</p></body></html>", "#missing")


def test_pdf_extraction_preserves_page_numbers() -> None:
    sections = extract_pdf(_minimal_pdf(["First page text", "Second page text"]))

    assert [(s.page_number, s.text) for s in sections] == [
        (1, "First page text"),
        (2, "Second page text"),
    ]


def test_chunks_never_cross_pages_and_long_text_overlaps() -> None:
    long_text = " ".join(f"Sentence number {i} about like-kind exchanges." for i in range(120))
    sections = [
        TextSection(text=long_text, page_number=1),
        TextSection(text="Short trailing note on page two.", page_number=2),
    ]

    chunks = chunk_sections(sections)

    page_one = [c for c in chunks if c.page_number == 1]
    assert len(page_one) > 1
    assert all(len(c.content) <= CHUNK_SIZE for c in chunks)
    assert page_one[0].content[-60:] in page_one[1].content  # overlap carries context
    assert chunks[-1].page_number == 2 and "page two" in chunks[-1].content
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_small_sections_merge_with_next_on_same_page() -> None:
    chunks = chunk_sections(
        [TextSection("Tiny intro.", section="Intro"), TextSection("x " * 200, section="Body")]
    )
    assert len(chunks) == 1
    assert chunks[0].section == "Intro" and chunks[0].content.startswith("Tiny intro.")


def test_committed_manifest_is_valid() -> None:
    manifest = load_manifest()
    assert 5 <= len(manifest.sources) <= 15


def test_local_embedder_is_deterministic_and_unit_length() -> None:
    embedder = LocalHashingEmbedder()
    a, b = embedder.embed_documents(["1031 exchange deadlines", "1031 exchange deadlines"])
    assert a == b
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0, rel_tol=1e-9)


class _FakeBedrockClient:
    def __init__(self) -> None:
        self.bodies: list[dict[str, object]] = []

    def invoke_model(self, **kwargs: object) -> dict[str, object]:
        self.bodies.append(json.loads(str(kwargs["body"])))
        return {"body": io.BytesIO(json.dumps({"embedding": [0.6, 0.8]}).encode())}


def test_bedrock_embedder_requests_titan_v2_dimensions_and_normalisation() -> None:
    client = _FakeBedrockClient()
    embedder = BedrockTitanEmbedder(client, "amazon.titan-embed-text-v2:0", dimensions=1024)

    vector = embedder.embed_query("What is a DST?")

    assert vector == [0.6, 0.8]
    assert client.bodies == [{"inputText": "What is a DST?", "dimensions": 1024, "normalize": True}]


def test_production_refuses_local_embeddings() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", embedding_provider="local")
