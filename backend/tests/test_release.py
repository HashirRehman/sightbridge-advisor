from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.core import release
from scripts.source_id import source_id


def test_release_marker_is_public_bounded_and_not_cached(
    client: TestClient, tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    marker = tmp_path / "release.json"
    monkeypatch.setattr(release, "RELEASE_PATH", marker)
    response = client.get("/api/release")
    assert response.status_code == 503 and response.json() == {"source_id": None}
    marker.write_text('{"source_id":"' + "a" * 64 + '","secret":"must not escape"}')
    response = client.get("/api/release")
    assert response.status_code == 200 and response.json() == {"source_id": "a" * 64}
    assert "no-store" in response.headers["Cache-Control"]
    marker.write_text('{"source_id":"not a release"}')
    assert client.get("/api/release").status_code == 503


def test_source_identity_changes_only_with_runtime_inputs(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "scripts").mkdir()
    for name in [
        "app/main.py",
        "pyproject.toml",
        "uv.lock",
        "scripts/build_lambda.sh",
        "scripts/source_id.py",
    ]:
        (tmp_path / name).write_text("original")
    initial = source_id(tmp_path)
    (tmp_path / "env.txt").write_text("private dummy credential")
    (tmp_path / "README.md").write_text("documentation")
    (tmp_path / "app/release.json").write_text("generated")
    assert source_id(tmp_path) == initial
    (tmp_path / "app/main.py").write_text("changed runtime")
    assert source_id(tmp_path) != initial
