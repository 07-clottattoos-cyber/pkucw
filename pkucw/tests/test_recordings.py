from __future__ import annotations

import json
import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch

from courseweb import recordings


class RecordingDownloadHelpersTest(unittest.TestCase):
    def test_request_bytes_retries_incomplete_read(self) -> None:
        with (
            patch.object(
                recordings,
                "_request_bytes",
                side_effect=[IncompleteRead(b"partial", 10), b"complete"],
            ) as request_bytes,
            patch.object(recordings.time, "sleep") as sleep,
        ):
            result = recordings._request_bytes_with_retries(
                "https://example.test/segment.ts",
                state={},
                headers={},
                timeout_seconds=1,
            )

        self.assertEqual(result, b"complete")
        self.assertEqual(request_bytes.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_request_bytes_reports_exhausted_retries(self) -> None:
        with (
            patch.object(
                recordings,
                "_request_bytes",
                side_effect=IncompleteRead(b"partial", 10),
            ),
            patch.object(recordings.time, "sleep"),
        ):
            with self.assertRaisesRegex(recordings.RecordingScrapeError, "已重试 2 次"):
                recordings._request_bytes_with_retries(
                    "https://example.test/segment.ts",
                    state={},
                    headers={},
                    timeout_seconds=1,
                    attempts=2,
                )

    def test_segment_checkpoint_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lecture.ts.part.json"
            recordings._save_segment_checkpoint(
                path,
                playlist_url="https://example.test/playlist.m3u8",
                total=10,
                completed=4,
            )

            self.assertEqual(
                recordings._load_segment_checkpoint(
                    path,
                    playlist_url="https://example.test/playlist.m3u8",
                    total=10,
                ),
                4,
            )
            self.assertEqual(json.loads(path.read_text())["completed"], 4)

    def test_segment_checkpoint_rejects_different_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lecture.ts.part.json"
            recordings._save_segment_checkpoint(
                path,
                playlist_url="https://example.test/old.m3u8",
                total=10,
                completed=4,
            )

            self.assertEqual(
                recordings._load_segment_checkpoint(
                    path,
                    playlist_url="https://example.test/new.m3u8",
                    total=10,
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
