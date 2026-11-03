"""Tests for the Bitbucket code paths in scripts/provider.py added by the
Bitbucket-fix PR #1 (fetch_pr_state, fetch_rollup, fetch_check_rows).

Follows the same style as tests/test_provider.py: no real network, no real
Bitbucket; mocks out provider._run so each test wires its own canned
subprocess response. The GitHub tests continue to pass unchanged.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import provider  # noqa: E402


def _done(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class ParseHelpersTests(unittest.TestCase):
    def test_bitbucket_slug_https(self):
        self.assertEqual(
            provider.bitbucket_slug("https://bitbucket.org/acme/widgets"),
            "acme/widgets",
        )
        self.assertEqual(
            provider.bitbucket_slug("https://bitbucket.org/acme/widgets.git"),
            "acme/widgets",
        )

    def test_bitbucket_slug_with_token_prefix(self):
        self.assertEqual(
            provider.bitbucket_slug(
                "https://x-token-auth:ATCTT3xFfGN0@bitbucket.org/acme/widgets"
            ),
            "acme/widgets",
        )

    def test_bitbucket_slug_ssh(self):
        self.assertEqual(
            provider.bitbucket_slug("git@bitbucket.org:acme/widgets.git"),
            "acme/widgets",
        )

    def test_bitbucket_slug_non_bitbucket(self):
        self.assertIsNone(provider.bitbucket_slug("https://github.com/acme/widgets"))
        self.assertIsNone(provider.bitbucket_slug(""))

    def test_bitbucket_pr_parts(self):
        self.assertEqual(
            provider.bitbucket_pr_parts(
                "https://bitbucket.org/acme/widgets/pull-requests/13"
            ),
            ("acme", "widgets", "13"),
        )
        self.assertEqual(
            provider.bitbucket_pr_parts(
                "https://bitbucket.org/acme/widgets/pullrequests/42"
            ),
            ("acme", "widgets", "42"),
        )
        self.assertEqual(
            provider.bitbucket_pr_parts(
                "https://bitbucket.org/acme/widgets/pull-requests/13/"
            ),
            ("acme", "widgets", "13"),
        )
        self.assertIsNone(provider.bitbucket_pr_parts("https://github.com/x/y/pull/1"))
        self.assertIsNone(provider.bitbucket_pr_parts(""))

    def test_bitbucket_token_from_env(self):
        with patch.dict("os.environ", {"BITBUCKET_TOKEN": "env-token"}):
            self.assertEqual(provider._bitbucket_token(Path(".")), "env-token")

    def test_bitbucket_token_from_warp_env_fallback(self):
        with patch.dict("os.environ", {"WARP_BITBUCKET_TOKEN": "warp-env-token"}, clear=False):
            with patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("BITBUCKET_TOKEN", None)
                self.assertEqual(provider._bitbucket_token(Path(".")), "warp-env-token")

    def test_bitbucket_token_from_remote_url(self):
        import os
        os.environ.pop("BITBUCKET_TOKEN", None)
        os.environ.pop("WARP_BITBUCKET_TOKEN", None)
        with patch.object(
            provider,
            "_run",
            return_value=_done(
                "https://x-token-auth:ATCTTxabcdef@bitbucket.org/acme/widgets\n"
            ),
        ):
            self.assertEqual(provider._bitbucket_token(Path(".")), "ATCTTxabcdef")

    def test_bitbucket_token_none_when_remote_is_plain_github(self):
        import os
        os.environ.pop("BITBUCKET_TOKEN", None)
        os.environ.pop("WARP_BITBUCKET_TOKEN", None)
        with patch.object(
            provider,
            "_run",
            return_value=_done("https://github.com/acme/widgets.git\n"),
        ):
            self.assertIsNone(provider._bitbucket_token(Path(".")))


_PR_MERGED_FIXTURE = {
    "type": "pullrequest",
    "id": 13,
    "state": "MERGED",
    "title": "IAP-1: Scaffold hello page",
    "created_on": "2026-11-03T15:58:43+00:00",
    "updated_on": "2026-11-03T16:29:46+00:00",
    "source": {
        "branch": {"name": "warp/IAP-1-IAP-1"},
        "commit": {"hash": "6b408cef69d5cc380e6e73a7f262fb764ad88651"},
    },
    "destination": {"branch": {"name": "main"}},
    "merge_commit": {"hash": "eb76afa13f09abc"},
}


_STATUSES_SUCCESS_FIXTURE = {
    "values": [
        {
            "key": "cursor-bugbot",
            "name": "Cursor Bugbot",
            "state": "SUCCESSFUL",
            "description": "No issues found",
            "created_on": "2026-11-03T16:30:00+00:00",
            "updated_on": "2026-11-03T16:31:00+00:00",
            "url": "https://cursor.com/bugbot/123",
        }
    ],
    "pagelen": 10,
    "page": 1,
    "size": 1,
}


_STATUSES_EMPTY_FIXTURE = {"values": [], "pagelen": 10, "page": 1, "size": 0}


_STATUSES_BUGBOT_FAILED_FIXTURE = {
    "values": [
        {
            "key": "cursor-bugbot",
            "name": "Cursor Bugbot",
            "state": "FAILED",
            "description": "Issues found",
        }
    ],
    "pagelen": 10,
    "page": 1,
    "size": 1,
}


class FetchPrStateBitbucketTests(unittest.TestCase):
    def setUp(self):
        import os
        os.environ["BITBUCKET_TOKEN"] = "unit-test-token"

    def tearDown(self):
        import os
        os.environ.pop("BITBUCKET_TOKEN", None)

    def _patched_run(self, pr_doc, statuses_doc):
        """Mock _run so the first curl returns the PR doc and the second returns statuses."""
        calls = {"n": 0}

        def fake_run(cmd, cwd=None, timeout=20):
            calls["n"] += 1
            if calls["n"] == 1:
                return _done(json.dumps(pr_doc))
            if calls["n"] == 2:
                return _done(json.dumps(statuses_doc))
            return _done("", returncode=1)

        return patch.object(provider, "_run", side_effect=fake_run)

    def test_merged_pr_with_green_bugbot(self):
        with self._patched_run(_PR_MERGED_FIXTURE, _STATUSES_SUCCESS_FIXTURE):
            snap = provider.fetch_pr_state(
                Path("."),
                "https://bitbucket.org/acme/widgets/pull-requests/13",
            )
        self.assertIsNotNone(snap)
        self.assertEqual(snap["prState"], "MERGED")
        self.assertEqual(snap["sha"], "6b408cef69d5cc380e6e73a7f262fb764ad88651")
        self.assertEqual(snap["mergeSha"], "eb76afa13f09abc")
        self.assertEqual(snap["rollup"], "green")
        self.assertEqual(snap["bugbot"], "pass")
        self.assertEqual(snap["mergedAt"], "2026-11-03T16:29:46+00:00")

    def test_merged_pr_with_no_statuses(self):
        with self._patched_run(_PR_MERGED_FIXTURE, _STATUSES_EMPTY_FIXTURE):
            snap = provider.fetch_pr_state(
                Path("."),
                "https://bitbucket.org/acme/widgets/pull-requests/13",
            )
        self.assertEqual(snap["prState"], "MERGED")
        self.assertEqual(snap["rollup"], "absent")
        self.assertEqual(snap["ci"], "absent")

    def test_open_pr_with_failed_bugbot(self):
        pr_open = dict(_PR_MERGED_FIXTURE, state="OPEN")
        pr_open.pop("merge_commit", None)
        with self._patched_run(pr_open, _STATUSES_BUGBOT_FAILED_FIXTURE):
            snap = provider.fetch_pr_state(
                Path("."),
                "https://bitbucket.org/acme/widgets/pull-requests/13",
            )
        self.assertEqual(snap["prState"], "OPEN")
        self.assertEqual(snap["bugbot"], "fail")
        self.assertEqual(snap["rollup"], "red")
        self.assertIn("findings", snap)
        self.assertEqual(len(snap["findings"]), 1)

    def test_declined_pr(self):
        pr_declined = dict(_PR_MERGED_FIXTURE, state="DECLINED")
        pr_declined.pop("merge_commit", None)
        with self._patched_run(pr_declined, _STATUSES_EMPTY_FIXTURE):
            snap = provider.fetch_pr_state(
                Path("."),
                "https://bitbucket.org/acme/widgets/pull-requests/13",
            )
        self.assertEqual(snap["prState"], "CLOSED")

    def test_api_401_returns_none(self):
        with patch.object(provider, "_run", return_value=_done("", returncode=22)):
            snap = provider.fetch_pr_state(
                Path("."),
                "https://bitbucket.org/acme/widgets/pull-requests/13",
            )
        self.assertIsNone(snap)

    def test_missing_token_returns_none(self):
        import os
        os.environ.pop("BITBUCKET_TOKEN", None)
        os.environ.pop("WARP_BITBUCKET_TOKEN", None)
        with patch.object(
            provider,
            "_run",
            return_value=_done("https://github.com/acme/widgets.git\n"),
        ):
            snap = provider.fetch_pr_state(
                Path("."),
                "https://bitbucket.org/acme/widgets/pull-requests/13",
            )
        self.assertIsNone(snap)

    def test_malformed_url_returns_none(self):
        snap = provider.fetch_pr_state(Path("."), "https://bitbucket.org/not-a-pr-url")
        self.assertIsNone(snap)


class FetchRollupBitbucketTests(unittest.TestCase):
    def setUp(self):
        import os
        os.environ["BITBUCKET_TOKEN"] = "unit-test-token"

    def tearDown(self):
        import os
        os.environ.pop("BITBUCKET_TOKEN", None)

    def test_fetch_rollup_no_longer_hardcodes_pending(self):
        """Before the fix, provider=bitbucket always returned pending.
        After the fix, it reflects the real check rollup."""
        calls = {"n": 0}

        def fake_run(cmd, cwd=None, timeout=20):
            calls["n"] += 1
            if calls["n"] == 1:
                return _done(json.dumps(_PR_MERGED_FIXTURE))
            if calls["n"] == 2:
                return _done(json.dumps(_STATUSES_SUCCESS_FIXTURE))
            return _done("", returncode=1)

        with patch.object(provider, "_run", side_effect=fake_run):
            rollup = provider.fetch_rollup(
                Path("."),
                "https://bitbucket.org/acme/widgets/pull-requests/13",
                "bitbucket",
            )
        self.assertEqual(rollup, "green")


class FetchCheckRowsBitbucketTests(unittest.TestCase):
    def setUp(self):
        import os
        os.environ["BITBUCKET_TOKEN"] = "unit-test-token"

    def tearDown(self):
        import os
        os.environ.pop("BITBUCKET_TOKEN", None)

    def test_fetch_check_rows_for_bitbucket_url(self):
        calls = {"n": 0}

        def fake_run(cmd, cwd=None, timeout=20):
            calls["n"] += 1
            if calls["n"] == 1:
                return _done(json.dumps(_PR_MERGED_FIXTURE))
            if calls["n"] == 2:
                return _done(json.dumps(_STATUSES_SUCCESS_FIXTURE))
            return _done("", returncode=1)

        with patch.object(provider, "_run", side_effect=fake_run):
            rows = provider.fetch_check_rows(
                Path("."),
                "https://bitbucket.org/acme/widgets/pull-requests/13",
                "bitbucket",
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Cursor Bugbot")
        self.assertEqual(rows[0]["conclusion"], "SUCCESSFUL")


if __name__ == "__main__":
    unittest.main()
