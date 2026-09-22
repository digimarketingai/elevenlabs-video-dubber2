from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
}

SHORT_HOSTS = {
    "youtu.be",
    "www.youtu.be",
}

EMBED_HOSTS = {
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


@dataclass(frozen=True)
class YouTubeURL:
    video_id: str
    canonical: str
    start_seconds: int
    kind: str


def parse_timestamp(value: str) -> int:
    value = str(value or "").strip().lower()

    if not value:
        return 0

    if value.isdigit():
        result = int(value)
    else:
        match = re.fullmatch(
            r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?",
            value,
        )

        if not match or not any(match.groups()):
            return 0

        hours, minutes, seconds = [
            int(item or 0)
            for item in match.groups()
        ]

        result = hours * 3600 + minutes * 60 + seconds

    # Reject unreasonable timestamp suggestions.
    return result if 0 <= result <= 7 * 24 * 3600 else 0


def normalize_youtube_url(value: str) -> YouTubeURL:
    value = str(value or "").strip()

    if not value:
        raise ValueError(
            "請貼上 YouTube 網址 / Paste a YouTube URL."
        )

    if len(value) > 4096 or any(char.isspace() for char in value):
        raise ValueError(
            "網址包含空白或過長 / URL contains whitespace or is too long."
        )

    if "://" not in value:
        value = "https://" + value

    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        raise ValueError(
            "無效網址 / Invalid URL."
        ) from None

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        raise ValueError(
            "僅接受一般 HTTP/HTTPS YouTube 網址 / "
            "Only standard HTTP/HTTPS YouTube URLs are accepted."
        )

    query = parse_qs(parsed.query)
    parts = [
        item for item in parsed.path.split("/")
        if item
    ]

    video_id = ""
    kind = ""

    if host in SHORT_HOSTS:
        if len(parts) == 1:
            video_id = parts[0]
            kind = "分享短網址 / Short sharing URL"

    elif host in YOUTUBE_HOSTS:
        if parsed.path.rstrip("/") == "/watch":
            values = query.get("v", [])

            if len(values) == 1:
                video_id = values[0]
                kind = "一般影片 / Watch URL"

        elif len(parts) == 2 and parts[0] in {
            "shorts", "embed", "live", "v"
        }:
            video_id = parts[1]

            kind = {
                "shorts": "Shorts 短片 / Shorts",
                "embed": "嵌入影片 / Embed",
                "live": "直播網址；僅接受已結束影片 / Live URL; recorded only",
                "v": "舊版影片網址 / Legacy video URL",
            }[parts[0]]

    elif host in EMBED_HOSTS:
        if len(parts) == 2 and parts[0] == "embed":
            video_id = parts[1]
            kind = "隱私嵌入網址 / Privacy-enhanced embed"

    else:
        raise ValueError(
            "網域不是支援的 YouTube 網域 / "
            "This is not a supported YouTube domain."
        )

    if not VIDEO_ID.fullmatch(video_id):
        raise ValueError(
            "找不到有效影片 ID。請使用影片、分享或 Shorts 網址，"
            "而非頻道或純播放清單網址。 / "
            "No valid video ID. Use a video, sharing, or Shorts URL, "
            "not a channel or playlist-only URL."
        )

    timestamp = ""

    for name in ("t", "start", "time_continue"):
        if query.get(name):
            timestamp = query[name][0]
            break

    if not timestamp and parsed.fragment:
        fragment = parse_qs(parsed.fragment)
        timestamp = fragment.get("t", [parsed.fragment])[0]

    return YouTubeURL(
        video_id=video_id,
        canonical=f"https://www.youtube.com/watch?v={video_id}",
        start_seconds=parse_timestamp(timestamp),
        kind=kind,
    )
