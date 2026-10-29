"""Trigger Amplify only after CI, then wait for the expected live commit."""

import os
import urllib.error
import urllib.request

from wait_for_release import ensure_current, wait_for_release


def main() -> None:
    revision = os.environ["RELEASE_SHA"]
    webhook = os.environ.get("AMPLIFY_WEBHOOK_URL", "")
    frontend = os.environ.get("FRONTEND_BASE_URL", "").rstrip("/")
    if not webhook.startswith("https://") or not frontend.startswith("https://"):
        raise SystemExit("Frontend deployment configuration is missing.")
    ensure_current()
    request = urllib.request.Request(  # noqa: S310 - HTTPS checked above.
        webhook,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30):  # noqa: S310
            pass
    except (OSError, urllib.error.URLError):
        raise SystemExit("Amplify did not accept the release trigger.") from None
    print("Amplify build requested; waiting for deployment verification.", flush=True)
    wait_for_release(frontend + "/api/release", "commit", revision, "Frontend")


if __name__ == "__main__":
    main()
