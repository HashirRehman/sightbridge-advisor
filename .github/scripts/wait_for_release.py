"""Verify a deployed revision, with bounded retries and no secret URL logging."""

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request


def current_revision() -> str:
    binary = shutil.which("gh")
    if binary is None:
        raise SystemExit("GitHub CLI is unavailable.")
    return subprocess.check_output(  # noqa: S603 - Fixed gh command, no shell.
        [
            binary,
            "api",
            f"repos/{os.environ['GITHUB_REPOSITORY']}/git/ref/heads/main",
            "--jq",
            ".object.sha",
        ],
        text=True,
    ).strip()


def ensure_current() -> None:
    if current_revision() != os.environ["RELEASE_SHA"]:
        raise SystemExit("Release superseded; no deployment success claimed.")


def wait_for_release(url: str, field: str, expected: str, service: str, timeout: int = 900) -> None:
    if not url.startswith("https://"):
        raise SystemExit("Release verification requires HTTPS.")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ensure_current()
        try:
            request = urllib.request.Request(  # noqa: S310 - HTTPS checked above.
                url, headers={"Cache-Control": "no-cache"}
            )
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                marker = json.load(response)
            if isinstance(marker, dict) and marker.get(field) == expected:
                print(f"{service}: expected source revision is live.", flush=True)
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        print(f"{service}: waiting for the expected source revision...", flush=True)
        time.sleep(20)
    raise SystemExit(f"{service}: verification timed out; no deployment success claimed.")
