import os

from wait_for_release import wait_for_release

base = os.environ.get("API_BASE_URL", "").rstrip("/")
expected = os.environ.get("EXPECTED_SOURCE_ID", "")
if not base.startswith("https://") or len(expected) != 64:
    raise SystemExit("Backend release verification configuration is missing.")
if os.environ.get("DEPLOYMENT_MODE") != "automatic":
    print(
        "No AWS credentials: verifying existing code or awaiting Terraform deployment.",
        flush=True,
    )
wait_for_release(base + "/api/release", "source_id", expected, "Backend")
