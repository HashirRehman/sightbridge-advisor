import json
import re
from pathlib import Path

RELEASE_PATH = Path(__file__).resolve().parents[1] / "release.json"


def release_source_id() -> str | None:
    try:
        data = json.loads(RELEASE_PATH.read_text())
    except (OSError, ValueError):
        return None
    value = data.get("source_id") if isinstance(data, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) else None
