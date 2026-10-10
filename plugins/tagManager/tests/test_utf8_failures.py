"""Synthetic response fixtures; every network call is mocked, including sync."""
import gzip
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from email.message import Message
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stashdb_api
import stashdb_scene_sync
import tag_manager
from sync_history import SyncHistory
from tests.test_stashdb_scene_sync import FakeStash, remote_tag, scene, tag

URL = "https://box.example/graphql"
BOX = {"endpoint": URL, "api_key": "fixture-key", "name": "Fixture box"}
BAD = b'{"data":{"value":"' + b"a" * (43452 - 18) + b'\xe2(\xa1"}}'


def response(body, **headers):
    result = MagicMock()
    result.status = 200
    result.headers = Message()
    result.headers["Content-Type"] = "application/json; charset=utf-8"
    result.headers["Content-Length"] = str(len(body))
    for key, value in headers.items():
        result.headers[key.replace("_", "-")] = value
    result.read.return_value = body
    result.__enter__.return_value = result
    result.__exit__.return_value = False
    return result


def payload(data):
    return response(json.dumps({"data": data}, ensure_ascii=False).encode("utf-8"))


@patch("stashdb_api.log")
@patch("stashdb_api.time.sleep")
@patch("stashdb_api.urllib.request.urlopen")
class TestUTF8Responses(unittest.TestCase):
    def test_invalid_then_valid_retries_strictly_and_closes_responses(self, urlopen, sleep, log):
        bad, good = response(BAD), payload({"value": "caf\u00e9 \U0001f431"})
        urlopen.side_effect = [bad, good]
        self.assertEqual(stashdb_api.graphql_request(URL, "query {}", api_key="fixture-key"),
                         {"value": "caf\u00e9 \U0001f431"})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1.0)
        bad.__exit__.assert_called_once()
        good.__exit__.assert_called_once()
        self.assertEqual(urlopen.call_args.args[0].get_header("Accept-encoding"), "identity")

    def test_exhaustion_is_typed_bounded_and_has_only_safe_metadata(self, urlopen, sleep, log):
        urlopen.return_value = response(BAD, Set_Cookie="fixture-secret", X_Request_Id="fixture-secret")
        with self.assertRaises(stashdb_api.StashDBAPIError) as caught:
            stashdb_api.graphql_request(URL, "query {}", api_key="fixture-key")
        error = caught.exception
        self.assertIsInstance(error, stashdb_api.StashDBResponseError)
        self.assertTrue(error.retryable)
        self.assertFalse(error.is_auth_error)
        self.assertIsNone(error.body)
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(1.0), call(2.0), call(4.0)])
        self.assertEqual(error.response_metadata, {
            "status": 200, "bytes": len(BAD), "content_type": "application/json",
            "content_encoding": "identity", "content_length": len(BAD),
            "decode_start": 43452, "decode_end": 43453, "gzip_magic": False,
        })
        self.assertIn("after 4 attempts", str(error))
        diagnostics = str(error) + str(log.mock_calls)
        self.assertNotIn("fixture-secret", diagnostics)
        self.assertNotIn("fixture-key", diagnostics)
        self.assertNotIn("a" * 30, diagnostics)

    def test_transport_and_decode_failures_share_one_retry_budget(self, urlopen, sleep, log):
        http_error = urllib.error.HTTPError(URL, 503, "unavailable", {}, io.BytesIO())
        urlopen.side_effect = [http_error, response(BAD), response(BAD), response(BAD)]
        with self.assertRaises(stashdb_api.StashDBAPIError):
            stashdb_api.graphql_request(URL, "query {}")
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(1.0), call(2.0), call(4.0)])

    def test_retry_delay_is_capped_and_zero_retries_still_raises_typed_error(self, urlopen, sleep, log):
        urlopen.return_value = response(BAD)
        with patch.dict(stashdb_api.DEFAULT_CONFIG, {"max_retry_delay": 2.0}):
            with self.assertRaises(stashdb_api.StashDBAPIError):
                stashdb_api.graphql_request(URL, "query {}")
        self.assertEqual(sleep.call_args_list, [call(1.0), call(2.0), call(2.0)])
        urlopen.reset_mock()
        sleep.reset_mock()
        with patch.dict(stashdb_api.DEFAULT_CONFIG, {"max_retries": 0}):
            with self.assertRaises(stashdb_api.StashDBResponseError):
                stashdb_api.graphql_request(URL, "query {}")
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()

    def test_gzip_bytes_are_rejected_without_payload_or_raw_header_values(self, urlopen, sleep, log):
        body = gzip.compress(b'{"data":{"value":"fixture-sensitive"}}', mtime=0)
        result = response(body)
        result.headers.replace_header("Content-Type", "fixture-secret\nforged-log")
        result.headers.replace_header("Content-Length", "fixture-secret")
        result.headers["Content-Encoding"] = "gzip"
        urlopen.return_value = result
        with self.assertRaises(stashdb_api.StashDBAPIError) as caught:
            stashdb_api.graphql_request(URL, "query {}")
        metadata = caught.exception.response_metadata
        self.assertEqual(metadata["content_type"], "other")
        self.assertIsNone(metadata["content_length"])
        self.assertEqual(metadata["content_encoding"], "gzip")
        self.assertTrue(metadata["gzip_magic"])
        self.assertNotIn("fixture-secret", str(caught.exception) + str(log.mock_calls))

    def test_failed_later_tag_page_returns_no_partial_list(self, urlopen, sleep, log):
        first = payload({"queryTags": {"count": 2, "tags": [{"id": "t1", "name": "One"}]}})
        urlopen.side_effect = [first] + [response(BAD) for _ in range(4)]
        with self.assertRaises(stashdb_api.StashDBAPIError):
            stashdb_api.query_all_tags(URL, "fixture-key", per_page=1)
        self.assertEqual(urlopen.call_count, 5)

    def test_invalid_http_error_body_is_not_lossily_decoded(self, urlopen, sleep, log):
        urlopen.side_effect = urllib.error.HTTPError(URL, 403, "forbidden", {}, io.BytesIO(BAD))
        with self.assertRaises(stashdb_api.StashDBAPIError) as caught:
            stashdb_api.graphql_request(URL, "query {}")
        self.assertIsNone(caught.exception.body)
        self.assertTrue(caught.exception.is_auth_error)
        self.assertNotIn("\ufffd", str(caught.exception))

    def test_json_error_keeps_existing_nonretry_behavior(self, urlopen, sleep, log):
        urlopen.return_value = response(b'{"data":')
        with self.assertRaises(stashdb_api.StashDBAPIError) as caught:
            stashdb_api.graphql_request(URL, "query {}")
        self.assertIn("Invalid JSON", str(caught.exception))
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()


@patch("stashdb_api.time.sleep")
@patch("stashdb_api.urllib.request.urlopen")
class TestSyncUTF8Failures(unittest.TestCase):
    def test_failed_batch_counts_scenes_and_continues_with_history(self, urlopen, sleep):
        client = FakeStash(tags=[tag("t1", "One")], scenes=[
            scene(f"s{i}", [(URL, f"r{i}")]) for i in range(42)
        ])
        successful = lambda i: {"id": f"r{i}", "tags": [remote_tag("rt1", "One")]}
        urlopen.side_effect = [response(BAD) for _ in range(4)] + [
            payload({"findScenesBySceneFingerprints": [[successful(40)], [successful(41)]]})
        ]
        with tempfile.TemporaryDirectory() as tmp, patch("stashdb_scene_sync.log") as log:
            history_path = os.path.join(tmp, "sync_history.sqlite")
            history = SyncHistory(history_path)
            try:
                stats = stashdb_scene_sync.sync_scene_tags(client, [BOX], {"dry_run": False}, history)
            finally:
                history.close()
            self.assertEqual((stats.errors, stats.processed, stats.updated, stats.tags_added_total),
                             (40, 2, 2, 2))
            self.assertTrue(stats.partial)
            self.assertTrue(stats.by_endpoint[URL]["partial"])
            self.assertEqual(stats.by_endpoint[URL]["errors"], 40)
            self.assertIn("incomplete", str(log.LogInfo.call_args_list).lower())
            self.assertNotIn("Sync complete!", str(log.LogInfo.call_args_list))
            reopened = SyncHistory(history_path)
            try:
                self.assertIsNone(reopened.get(URL, "s0", "r0"))
                self.assertEqual(reopened.get(URL, "s40", "r40"), {"rt1"})
                self.assertEqual(reopened.get(URL, "s41", "r41"), {"rt1"})
            finally:
                reopened.close()
        self.assertEqual(urlopen.call_count, 5)
        self.assertEqual(client.add_calls, [("s40", ["t1"]), ("s41", ["t1"])])

    def test_failed_id_lookup_retains_prior_updates_and_returns_incomplete(self, urlopen, sleep):
        client = FakeStash(tags=[tag("t1", "One")], scenes=[
            scene(f"s{i}", [(URL, f"r{i}")], fingerprints=False) for i in range(3)
        ])
        client.config = {"general": {"apiKey": "fixture-local-key", "stashBoxes": [BOX]},
                         "plugins": {"tagManager": {"syncDryRun": False}}}
        urlopen.side_effect = [payload({"findScene": {"id": "r0", "tags": [remote_tag("rt1", "One")]}})] + [
            response(BAD) for _ in range(4)
        ] + [payload({"findScene": {"id": "r2", "tags": [remote_tag("rt1", "One")]}})]
        with tempfile.TemporaryDirectory() as tmp, patch("tag_manager.LocalStash", return_value=client), \
                patch("tag_manager.get_sync_history_path", return_value=os.path.join(tmp, "history.sqlite")):
            result = tag_manager.handle_sync_scene_tags({"Host": "local.example"})
            self.assertEqual((result["errors"], result["updated"], result["tags_added"]), (1, 2, 2))
            self.assertTrue(result["partial"])
            self.assertIn("incomplete", result["error"].lower())
            self.assertNotIn("success", result)
        self.assertEqual(client.add_calls, [("s0", ["t1"]), ("s2", ["t1"])])

    def test_exhaustion_after_two_updates_retains_26_tags_and_durable_history(self, urlopen, sleep):
        local_tags = [tag(f"t{i}", f"Tag {i}") for i in range(13)]
        remote_tags = [remote_tag(f"rt{i}", f"Tag {i}") for i in range(13)]
        client = FakeStash(tags=local_tags, scenes=[
            scene(f"s{i}", [(URL, f"r{i}")], fingerprints=False) for i in range(3)
        ])
        urlopen.side_effect = [payload({"findScene": {"id": f"r{i}", "tags": remote_tags}})
                               for i in range(2)] + [response(BAD) for _ in range(4)]
        with tempfile.TemporaryDirectory() as tmp:
            history_path = os.path.join(tmp, "history.sqlite")
            history = SyncHistory(history_path)
            try:
                stats = stashdb_scene_sync.sync_scene_tags(client, [BOX], {"dry_run": False}, history)
            finally:
                history.close()
            self.assertEqual((stats.updated, stats.tags_added_total, stats.errors), (2, 26, 1))
            self.assertTrue(stats.partial)
            reopened = SyncHistory(history_path)
            try:
                self.assertEqual(reopened.get(URL, "s0", "r0"), {f"rt{i}" for i in range(13)})
                self.assertEqual(reopened.get(URL, "s1", "r1"), {f"rt{i}" for i in range(13)})
                self.assertIsNone(reopened.get(URL, "s2", "r2"))
            finally:
                reopened.close()

    def test_all_failed_lookups_report_errors_without_writes_or_history(self, urlopen, sleep):
        client = FakeStash(tags=[tag("t1", "One")], scenes=[
            scene("s0", [(URL, "r0")], fingerprints=False)
        ])
        client.config = {"general": {"apiKey": "fixture-local-key", "stashBoxes": [BOX]},
                         "plugins": {"tagManager": {"syncDryRun": True}}}
        urlopen.side_effect = [response(BAD) for _ in range(4)]
        with tempfile.TemporaryDirectory() as tmp, patch("tag_manager.LocalStash", return_value=client), \
                patch("tag_manager.get_sync_history_path", return_value=os.path.join(tmp, "history.sqlite")):
            result = tag_manager.handle_sync_scene_tags({"Host": "local.example"})
            self.assertEqual((result["errors"], result["updated"], result["processed"]), (1, 0, 0))
            self.assertTrue(result["partial"])
            self.assertIn("All 1 scenes failed", result["error"])
            self.assertNotIn("success", result)
        self.assertEqual(client.add_calls, [])


if __name__ == "__main__":
    unittest.main()
