"""Fingerprint the actual Lambda sources, excluding secrets, tests and generated files."""

import argparse
import hashlib
import json
from pathlib import Path


def source_id(root: Path) -> str:
    files = list((root / "app").rglob("*.py"))
    files += [
        root / name
        for name in ("pyproject.toml", "uv.lock", "scripts/build_lambda.sh", "scripts/source_id.py")
    ]
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, "big") + name)
        digest.update(len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    value = source_id(Path(__file__).resolve().parents[1])
    print(json.dumps({"source_id": value}) if args.json else value)
