import unittest

from youtube_urls import normalize_youtube_url, parse_timestamp


VIDEO_ID = "dQw4w9WgXcQ"
CANONICAL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


class YouTubeURLTests(unittest.TestCase):
    def test_supported_formats(self):
        examples = [
            f"https://www.youtube.com/watch?v={VIDEO_ID}",
            f"https://youtube.com/watch?v={VIDEO_ID}&feature=shared",
            f"https://youtu.be/{VIDEO_ID}?si=example",
            f"https://www.youtube.com/shorts/{VIDEO_ID}?si=example",
            f"https://m.youtube.com/watch?v={VIDEO_ID}",
            f"https://music.youtube.com/watch?v={VIDEO_ID}",
            f"https://www.youtube.com/embed/{VIDEO_ID}",
            f"https://www.youtube-nocookie.com/embed/{VIDEO_ID}",
            f"https://www.youtube.com/live/{VIDEO_ID}",
            f"youtu.be/{VIDEO_ID}",
            f"youtube.com/shorts/{VIDEO_ID}",
        ]

        for value in examples:
            with self.subTest(value=value):
                result = normalize_youtube_url(value)
                self.assertEqual(result.video_id, VIDEO_ID)
                self.assertEqual(result.canonical, CANONICAL)

    def test_timestamps(self):
        examples = [
            (f"https://youtu.be/{VIDEO_ID}?t=90", 90),
            (f"https://youtu.be/{VIDEO_ID}?t=1m30s", 90),
            (f"https://youtube.com/watch?v={VIDEO_ID}&start=12", 12),
            (f"https://youtu.be/{VIDEO_ID}#t=2m", 120),
        ]

        for value, expected in examples:
            with self.subTest(value=value):
                self.assertEqual(
                    normalize_youtube_url(value).start_seconds,
                    expected,
                )

    def test_video_link_with_playlist_is_single_video(self):
        result = normalize_youtube_url(
            f"https://youtube.com/watch?v={VIDEO_ID}&list=PLexample"
        )
        self.assertEqual(result.canonical, CANONICAL)

    def test_rejected_inputs(self):
        examples = [
            "",
            "https://example.com/watch?v=" + VIDEO_ID,
            "https://youtube.com.evil.example/watch?v=" + VIDEO_ID,
            "https://youtube.com@evil.example/watch?v=" + VIDEO_ID,
            "https://user:password@youtube.com/watch?v=" + VIDEO_ID,
            "file:///etc/passwd",
            "javascript:alert(1)",
            "https://youtube.com/playlist?list=PLexample",
            "https://youtube.com/@example",
            "https://youtu.be/short",
            f"https://youtube.com/watch?v={VIDEO_ID}&v={VIDEO_ID}",
            f"https://youtube.com:9999/watch?v={VIDEO_ID}",
            f"https://youtu.be/{VIDEO_ID}/extra",
            f"https://youtu.be/{VIDEO_ID} extra text",
        ]

        for value in examples:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_youtube_url(value)

    def test_timestamp_parser(self):
        self.assertEqual(parse_timestamp("1h2m3s"), 3723)
        self.assertEqual(parse_timestamp("90"), 90)
        self.assertEqual(parse_timestamp("invalid"), 0)
        self.assertEqual(parse_timestamp("-1"), 0)
        self.assertEqual(parse_timestamp("999999999"), 0)


if __name__ == "__main__":
    unittest.main()
