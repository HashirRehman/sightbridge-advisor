import io
import os
import unittest
from unittest.mock import patch

import deploy_frontend
import wait_for_release


class ReleaseStatusTests(unittest.TestCase):
    def test_matching_live_marker_is_required(self):
        response = io.BytesIO(b'{"commit":"expected"}')
        with (
            patch.object(wait_for_release, "ensure_current"),
            patch.object(wait_for_release.urllib.request, "urlopen", return_value=response),
        ):
            wait_for_release.wait_for_release(
                "https://example.test/api/release", "commit", "expected", "Frontend"
            )

    def test_wrong_marker_times_out_instead_of_claiming_success(self):
        with (
            patch.object(wait_for_release, "ensure_current"),
            patch.object(
                wait_for_release.urllib.request,
                "urlopen",
                return_value=io.BytesIO(b'{"commit":"old"}'),
            ),
            patch.object(wait_for_release.time, "monotonic", side_effect=[0, 1, 1001]),
            patch.object(wait_for_release.time, "sleep"),
            self.assertRaisesRegex(SystemExit, "timed out"),
        ):
            wait_for_release.wait_for_release(
                "https://example.test/api/release", "commit", "new", "Frontend"
            )

    def test_unavailable_endpoint_does_not_count_as_deployed(self):
        with (
            patch.object(wait_for_release, "ensure_current"),
            patch.object(
                wait_for_release.urllib.request, "urlopen", side_effect=OSError("unavailable")
            ),
            patch.object(wait_for_release.time, "monotonic", side_effect=[0, 1, 1001]),
            patch.object(wait_for_release.time, "sleep"),
            self.assertRaisesRegex(SystemExit, "no deployment success"),
        ):
            wait_for_release.wait_for_release(
                "https://example.test/api/release", "source_id", "new", "Backend"
            )

    def test_superseded_revision_aborts(self):
        with (
            patch.dict(os.environ, {"RELEASE_SHA": "new"}),
            patch.object(wait_for_release, "current_revision", return_value="other"),
            self.assertRaisesRegex(SystemExit, "superseded"),
        ):
            wait_for_release.ensure_current()

    def test_missing_webhook_does_not_start_build(self):
        with (
            patch.dict(
                os.environ,
                {
                    "RELEASE_SHA": "new",
                    "AMPLIFY_WEBHOOK_URL": "",
                    "FRONTEND_BASE_URL": "https://example.test",
                },
            ),
            patch.object(deploy_frontend.urllib.request, "urlopen") as request,
            self.assertRaisesRegex(SystemExit, "configuration"),
        ):
            deploy_frontend.main()
        request.assert_not_called()

    def test_trigger_failure_does_not_start_success_poll(self):
        with (
            patch.dict(
                os.environ,
                {
                    "RELEASE_SHA": "new",
                    "AMPLIFY_WEBHOOK_URL": "https://example.test/private",
                    "FRONTEND_BASE_URL": "https://example.test",
                },
            ),
            patch.object(deploy_frontend, "ensure_current"),
            patch.object(
                deploy_frontend.urllib.request, "urlopen", side_effect=OSError("private URL")
            ),
            patch.object(deploy_frontend, "wait_for_release") as poll,
            self.assertRaisesRegex(SystemExit, "did not accept"),
        ):
            deploy_frontend.main()
        poll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
