import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "data" / "sources.json"


class SourceSpec(BaseModel):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,99}$")
    title: str
    publisher: str
    url: HttpUrl
    document_type: Literal["html", "pdf"]
    topics: list[str]
    content_selector: str | None = None

    @model_validator(mode="after")
    def html_needs_selector(self) -> "SourceSpec":
        if self.document_type == "html" and not self.content_selector:
            raise ValueError(f"{self.source_id}: HTML sources need a content_selector")
        return self


class SourceManifest(BaseModel):
    description: str
    sources: list[SourceSpec] = Field(min_length=5, max_length=15)

    @model_validator(mode="after")
    def unique_ids(self) -> "SourceManifest":
        ids = [s.source_id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate source_id in manifest")
        return self


def load_manifest(path: Path = DEFAULT_MANIFEST) -> SourceManifest:
    return SourceManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
