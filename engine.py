from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
import srt
from opencc import OpenCC
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from youtube_urls import normalize_youtube_url


ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)

API = "https://api.elevenlabs.io/v1"

MAX_SOURCE_BYTES = 500 * 1024 * 1024
MAX_YOUTUBE_SECONDS = 20 * 60

MODEL = None
CONVERTER = OpenCC("s2t")

LANGUAGES = [
    ("英文 / English", "en"),
    ("中文 / Chinese", "zh"),
    ("日文 / Japanese", "ja"),
    ("韓文 / Korean", "ko"),
    ("西班牙文 / Spanish", "es"),
    ("法文 / French", "fr"),
    ("德文 / German", "de"),
    ("義大利文 / Italian", "it"),
    ("葡萄牙文 / Portuguese", "pt"),
    ("阿拉伯文 / Arabic", "ar"),
    ("印地文 / Hindi", "hi"),
]

SUBTITLE_CHOICES = [
    ("翻譯字幕 / Translated", "translated"),
    ("原文字幕 / Original", "original"),
    ("雙語字幕 / Bilingual", "bilingual"),
    ("無字幕 / None", "none"),
]

AUDIO_CHOICES = [
    ("配音 / Dubbed", "dubbed"),
    ("原音 / Original", "original"),
]


def safe_error(error, key=""):
    text = str(error)

    if key:
        text = text.replace(key, "[REDACTED]")

    # Avoid exposing signed storage URLs or long query strings.
    text = re.sub(r"https?://\S+", "[URL]", text)
    return text[-1800:]


def run_command(args, cwd=None, timeout=1800):
    try:
        result = subprocess.run(
            [str(item) for item in args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            "處理逾時 / Processing timed out."
        ) from None

    if result.returncode:
        detail = safe_error(result.stderr or result.stdout)
        raise RuntimeError(
            "處理失敗 / Processing failed:\n" + detail
        )

    return result.stdout


def ffmpeg(args, cwd=None):
    return run_command(
        ["ffmpeg", "-nostdin", "-y", "-v", "error"] + args,
        cwd=cwd,
    )


def probe(path):
    return json.loads(
        run_command(
            [
                "ffprobe",
                "-v", "error",
                "-protocol_whitelist", "file,pipe",
                "-show_format",
                "-show_streams",
                "-of", "json",
                path,
            ],
            timeout=60,
        )
    )


def duration(path):
    value = float(probe(path)["format"]["duration"])

    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            "無法讀取有效媒體長度 / Invalid media duration."
        )

    return value


def save_job(job):
    if not job.get("directory"):
        return

    destination = Path(job["directory"]) / "job.json"
    temporary = destination.with_suffix(".tmp")

    temporary.write_text(
        json.dumps(job, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    temporary.replace(destination)


# ------------------------------------------------------------
# YouTube
# ------------------------------------------------------------

def ytdlp_base():
    args = [
        sys.executable,
        "-m", "yt_dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--socket-timeout", "25",
        "--retries", "2",
        "--fragment-retries", "2",
    ]

    if shutil.which("deno"):
        args += ["--js-runtimes", "deno"]
    elif shutil.which("node"):
        args += ["--js-runtimes", "node"]

    return args


def inspect_youtube(url):
    normalized = normalize_youtube_url(url)

    try:
        metadata = json.loads(
            run_command(
                ytdlp_base()
                + [
                    "--dump-single-json",
                    "--skip-download",
                    normalized.canonical,
                ],
                timeout=180,
            )
        )
    except Exception as exc:
        raise RuntimeError(
            "網址格式有效，但 YouTube 可用性檢查失敗。"
            "可能需要登入、影片已移除，或目前網路遭到阻擋。"
            "請改用您有權使用的本機影片。 / "
            "URL format is valid, but YouTube availability checking failed. "
            "The video may require sign-in, be unavailable, or be blocked "
            "from this network. Upload an authorized local video instead.\n"
            + safe_error(exc)
        ) from None

    seconds = float(metadata.get("duration") or 0)

    if (
        metadata.get("is_live")
        or metadata.get("live_status") in {"is_live", "is_upcoming"}
        or not math.isfinite(seconds)
        or seconds <= 0
        or seconds > MAX_YOUTUBE_SECONDS
    ):
        raise ValueError(
            "本工具僅接受已發布、非直播且不超過 20 分鐘的影片。 / "
            "This app accepts available, non-live videos up to 20 minutes."
        )

    return normalized, metadata


def download_youtube(url, directory, emit):
    emit("檢查 YouTube 影片 / Checking YouTube video")
    normalized, metadata = inspect_youtube(url)

    emit(
        "下載影片 / Downloading: "
        + str(metadata.get("title") or normalized.video_id)
    )

    try:
        run_command(
            ytdlp_base()
            + [
                "-f",
                (
                    "bv*[height<=1280][width<=1280]+ba/"
                    "b[height<=1280][width<=1280]"
                ),
                "--merge-output-format", "mp4",
                # This applies per download, not to all temporary files.
                "--max-filesize", "250M",
                "-o", str(directory / "source.%(ext)s"),
                normalized.canonical,
            ],
            timeout=1200,
        )
    except Exception as exc:
        raise RuntimeError(
            "YouTube 下載失敗；網址有效不代表一定能下載。"
            "請改用本機上傳。 / "
            "YouTube download failed; a valid URL does not guarantee "
            "download access. Use a local upload.\n"
            + safe_error(exc)
        ) from None

    candidates = [
        path for path in directory.glob("source.*")
        if path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
    ]

    if not candidates:
        raise RuntimeError(
            "找不到下載影片 / No downloaded video was found."
        )

    source = max(candidates, key=lambda path: path.stat().st_size)

    return source, {
        "youtube_url": normalized.canonical,
        "title": str(metadata.get("title") or ""),
    }


# ------------------------------------------------------------
# ElevenLabs
# ------------------------------------------------------------

def api_session(key):
    key = str(key or "").strip()

    if not key:
        raise ValueError(
            "請輸入 ElevenLabs API 金鑰 / Enter an ElevenLabs API key."
        )

    session = requests.Session()
    session.headers["xi-api-key"] = key

    # GET retries only. Never automatically retry paid POST requests.
    retries = Retry(
        total=2,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )

    session.mount(
        "https://",
        HTTPAdapter(max_retries=retries),
    )

    return session


def api_json(session, method, path, **kwargs):
    timeout = kwargs.pop("timeout", (20, 180))

    try:
        response = session.request(
            method,
            API + path,
            timeout=timeout,
            allow_redirects=False,
            **kwargs,
        )
    except requests.RequestException:
        extra = ""

        if method == "POST":
            extra = (
                " 伺服器可能已接受專案；請先檢查帳戶，避免重複扣款。 / "
                "The server may already have accepted the project. "
                "Check your account before creating another."
            )

        raise RuntimeError(
            "ElevenLabs 連線失敗 / ElevenLabs connection failed."
            + extra
        ) from None

    if not 200 <= response.status_code < 300:
        raise RuntimeError(
            f"ElevenLabs HTTP {response.status_code}："
            "請檢查金鑰、權限、方案及額度。 / "
            "Check your API key, permissions, plan, and allowance."
        )

    try:
        return response.json()
    except ValueError:
        raise RuntimeError(
            "ElevenLabs 回傳非 JSON 資料 / Invalid ElevenLabs JSON response."
        ) from None


def quota_html(key):
    try:
        with api_session(key) as session:
            data = api_json(
                session,
                "GET",
                "/user/subscription",
                timeout=(15, 30),
            )

        used = int(data["character_count"])
        limit = int(data["character_limit"])

        if used < 0 or limit < 0:
            raise ValueError("Invalid quota")

        remaining = max(0, limit - used)
        percent = min(100, used / limit * 100) if limit else 0

        tier = html.escape(str(data.get("tier", "—")))
        status = html.escape(str(data.get("status", "—")))

        timestamp = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

        extension = data.get("max_credit_limit_extension", "未提供 / Not provided")
        extension = html.escape(str(extension))

        return f"""
        <div class="quota-card">
          <div class="eyebrow">ELEVENLABS · 額度 / ALLOWANCE</div>
          <div class="quota-number">{remaining:,}</div>
          <div>剩餘內含額度 / Remaining included allowance</div>
          <div class="quota-grid">
            <div><b>{used:,}</b><small>已使用 / Used</small></div>
            <div><b>{limit:,}</b><small>本期上限 / Limit</small></div>
            <div><b>{tier}</b><small>方案 / Plan</small></div>
          </div>
          <progress value="{percent:.2f}" max="100"></progress>
          <p>帳戶狀態 / Account status: {status}<br>
          額外用量上限設定 / Overage cap setting: {extension}</p>
          <small>
            更新 / Updated: {timestamp}<br>
            依 API 的 character_count / character_limit 計算，
            不是 LLM Token，也不是保證可花費的總額。<br>
            Based on API character_count / character_limit,
            not LLM tokens or a guaranteed total spending limit.
            API-key caps and other account restrictions may differ.
          </small>
        </div>
        """

    except Exception as exc:
        message = html.escape(safe_error(exc, str(key or "")))

        return (
            '<div class="quota-card">'
            "<b>無法讀取額度 / Allowance unavailable</b>"
            f"<p>{message}</p>"
            "<small>額度查詢需要帳戶讀取權限；"
            "配音權限與額度查詢權限可能不同。 / "
            "Subscription access is required; "
            "dubbing permissions may differ.</small>"
            "</div>"
        )


def fetch_audio(url, output):
    parsed = urlsplit(url)

    if parsed.scheme != "https" or not parsed.hostname:
        raise RuntimeError(
            "無效音訊下載網址 / Invalid audio download URL."
        )

    # Deliberately use a separate request without xi-api-key.
    try:
        with requests.get(
            url,
            stream=True,
            timeout=(20, 180),
        ) as response:
            response.raise_for_status()
            received = 0

            with output.open("wb") as file:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue

                    received += len(chunk)

                    if received > 1024 * 1024 * 1024:
                        raise RuntimeError(
                            "音訊超過 1 GB / Audio exceeds 1 GB."
                        )

                    file.write(chunk)

    except requests.RequestException:
        raise RuntimeError(
            "音訊下載失敗，請按繼續查詢。 / "
            "Audio download failed. Use Resume."
        ) from None


# ------------------------------------------------------------
# Media preparation
# ------------------------------------------------------------

def prepare(job, config, emit):
    if not config["permission"]:
        raise ValueError(
            "請先確認內容及聲音授權 / Confirm content and voice permission."
        )

    start = float(config["start"] or 0)
    length = float(config["length"] or 20)

    if not math.isfinite(start) or start < 0:
        raise ValueError(
            "開始時間無效 / Invalid start time."
        )

    if not math.isfinite(length) or not 5 <= length <= 120:
        raise ValueError(
            "片段長度須為 5–120 秒 / Clip length must be 5–120 seconds."
        )

    directory = Path(tempfile.mkdtemp(prefix="v2_", dir=WORK))

    job.clear()
    job.update({
        "directory": str(directory),
        "original_rows": [],
        "translated_rows": [],
    })

    if config["input_mode"] == "youtube":
        source, metadata = download_youtube(
            config["url"], directory, emit
        )
        job.update(metadata)
    else:
        if not config["upload"]:
            raise ValueError(
                "請先上傳影片 / Upload a video first."
            )

        source = Path(config["upload"])
        job["title"] = source.name

    if not source.is_file() or source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(
            "來源檔案須小於 500 MB / Source file must be under 500 MB."
        )

    information = probe(source)
    types = {
        stream.get("codec_type")
        for stream in information.get("streams", [])
    }

    if not {"video", "audio"}.issubset(types):
        raise ValueError(
            "影片必須包含影像及音訊 / Video must contain video and audio."
        )

    total = duration(source)

    if start >= total:
        raise ValueError(
            "開始時間超過影片長度 / Start time exceeds video duration."
        )

    length = min(length, total - start)
    edge = 854 if config["quality"] == "480" else 1280

    clip = directory / "original.mp4"
    audio = directory / "original.m4a"

    emit("準備影片；保留橫向或直向比例 / Preparing landscape or portrait clip")

    # Bounding square preserves portrait Shorts without stretching.
    video_filter = (
        f"scale=w='min({edge},iw)':h='min({edge},ih)':"
        "force_original_aspect_ratio=decrease:"
        "force_divisible_by=2,fps=30,setsar=1"
    )

    ffmpeg([
        "-ss", str(start),
        "-protocol_whitelist", "file,pipe",
        "-i", source,
        "-t", str(length),
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-vf", video_filter,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "25",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        clip,
    ])

    ffmpeg([
        "-i", clip,
        "-map", "0:a:0",
        "-vn",
        "-c:a", "copy",
        audio,
    ])

    job.update({
        "clip": str(clip),
        "source_audio": str(audio),
        "duration": duration(clip),
        "source_language": (
            None if config["source"] == "auto"
            else config["source"]
        ),
    })

    save_job(job)

    # Delete downloaded source fragments after successful preparation.
    if config["input_mode"] == "youtube":
        for path in directory.glob("source.*"):
            if path.is_file():
                path.unlink(missing_ok=True)


def create_dub(job, config, emit):
    if not job.get("clip"):
        raise ValueError(
            "請先準備影片 / Prepare a clip first."
        )

    if job.get("project_id") or job.get("creation_uncertain"):
        raise ValueError(
            "此片段已有專案或建立結果不明。請使用繼續查詢，"
            "或先在 ElevenLabs 確認，再重新準備片段。 / "
            "This clip already has a project or an uncertain creation result. "
            "Use Resume, or check ElevenLabs before preparing another clip."
        )

    if not config["permission"] or not config["paid"]:
        raise ValueError(
            "請確認內容授權及付費配音 / Confirm permission and paid dubbing."
        )

    target = config["target"]

    if target == job.get("source_language"):
        raise ValueError(
            "來源與目標語言不可相同 / Source and target languages must differ."
        )

    with api_session(config["key"]) as session:
        emit("建立付費配音專案 / Creating paid dubbing project")

        data = {
            "reference": "digimarketingai Video Dubber v2",
            "model_id": "dubbing_v2",
            "target_language": target,
        }

        if job.get("source_language"):
            data["source_language"] = job["source_language"]

        job["target_language"] = target

        # Set before POST so an uncertain network result cannot be
        # accidentally retried through this session.
        job["creation_uncertain"] = True
        save_job(job)

        with open(job["source_audio"], "rb") as file:
            result = api_json(
                session,
                "POST",
                "/dubbing/project",
                data=data,
                files={
                    "file": ("audio.m4a", file, "audio/mp4")
                },
                timeout=(30, 300),
            )

        job["project_id"] = result["project_id"]
        job["creation_uncertain"] = False

        language_ids = result.get("language_ids") or []

        if language_ids:
            job["language_id"] = language_ids[0]

        save_job(job)

    emit("專案已建立 / Project created: " + job["project_id"])
    resume_dub(job, config, emit)


def resume_dub(job, config, emit):
    if not job.get("clip") or not job.get("project_id"):
        raise ValueError(
            "目前工作階段沒有可繼續的專案。"
            "若建立請求逾時且沒有 ID，請先檢查 ElevenLabs 帳戶。 / "
            "No resumable project in this session. If creation timed out "
            "without an ID, check your ElevenLabs account."
        )

    if job.get("dub_video") and Path(job["dub_video"]).is_file():
        emit("使用已完成配音 / Using completed dub")
        return

    project_id = job["project_id"]
    deadline = time.monotonic() + 1800
    previous = None
    target_data = None

    with api_session(config["key"]) as session:
        while time.monotonic() < deadline:
            project = api_json(
                session, "GET", f"/dubbing/project/{project_id}"
            )

            if project.get("status") == "failed":
                raise RuntimeError(
                    "配音專案失敗，請檢查 ElevenLabs。 / "
                    "Dubbing project failed. Check ElevenLabs."
                )

            if not job.get("language_id"):
                ids = project.get("language_ids") or []

                if ids:
                    job["language_id"] = ids[0]
                    save_job(job)

            target_status = "waiting"

            if job.get("language_id"):
                language_id = job["language_id"]

                target_data = api_json(
                    session,
                    "GET",
                    f"/dubbing/project/{project_id}/language/{language_id}",
                )

                target_status = target_data.get("status", "unknown")

            current = (
                f"來源狀態 / Source: {project.get('status')} · "
                f"配音狀態 / Dub: {target_status}"
            )

            if current != previous:
                emit(current)
                previous = current

            if target_status == "completed":
                break

            if target_status in {"failed", "stale"}:
                raise RuntimeError(
                    f"配音狀態 / Dub status: {target_status}。"
                    "本工具不會自動付費重新生成。 / "
                    "This app will not automatically regenerate a paid dub."
                )

            time.sleep(8)

        else:
            raise TimeoutError(
                "已查詢 30 分鐘；雲端可能仍在處理。請按繼續查詢。 / "
                "Polling stopped after 30 minutes; the cloud job may still "
                "be running. Use Resume."
            )

    url = ((target_data or {}).get("outputs") or {}).get("lossless_audio")

    if not url:
        raise RuntimeError(
            "完成的專案未提供音訊 / Completed target has no audio output."
        )

    directory = Path(job["directory"])
    audio = directory / "dubbed_audio.bin"
    output = directory / "dubbed.mp4"

    emit("下載配音音訊 / Downloading dubbed audio")
    fetch_audio(url, audio)

    audio_duration = duration(audio)

    if abs(audio_duration - job["duration"]) > 1:
        emit(
            "提醒：配音與影片長度不同；較長音訊會裁切，較短音訊會補靜音。 / "
            "Warning: durations differ; longer audio is trimmed and "
            "shorter audio is padded with silence."
        )

    emit("合併影片與配音 / Assembling dubbed video")

    ffmpeg([
        "-i", job["clip"],
        "-i", audio,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-af", "apad",
        "-t", str(job["duration"]),
        "-movflags", "+faststart",
        output,
    ])

    probe(output)
    job["dub_video"] = str(output)
    save_job(job)


# ------------------------------------------------------------
# Subtitle recognition and validation
# ------------------------------------------------------------

def plain_text(value):
    text = str(value or "").replace("\x00", "").replace("\r", "")
    text = html.unescape(text)
    text = re.sub(r"<[^>]*>", "", text)
    return text.strip()


def validate_rows(rows, limit=None):
    output = []

    for index, row in enumerate(rows or [], 1):
        if len(row) < 3 or not plain_text(row[2]):
            continue

        try:
            start, end = float(row[0]), float(row[1])
        except (TypeError, ValueError):
            raise ValueError(
                f"第 {index} 列時間無效 / Invalid times in row {index}."
            ) from None

        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
        ):
            raise ValueError(
                f"第 {index} 列須符合 0 ≤ 開始 < 結束 / "
                f"Row {index} must satisfy 0 ≤ start < end."
            )

        if limit is not None:
            if start >= limit:
                continue
            end = min(end, limit)

        start, end = round(start, 3), round(end, 3)

        if end > start:
            output.append([start, end, plain_text(row[2])])

    if len(output) > 2000:
        raise ValueError(
            "字幕最多 2,000 列 / Maximum 2,000 subtitle rows."
        )

    return sorted(output, key=lambda row: (row[0], row[1]))


def transcribe(path, language, traditional, emit):
    global MODEL

    if MODEL is None:
        emit(
            "載入 Whisper small；首次執行需要下載模型 / "
            "Loading Whisper small; first run downloads the model"
        )

        from faster_whisper import WhisperModel

        MODEL = WhisperModel(
            "small",
            device="cpu",
            compute_type="int8",
            cpu_threads=min(4, os.cpu_count() or 2),
        )

    segments, info = MODEL.transcribe(
        path,
        language=language,
        task="transcribe",
        beam_size=3,
        vad_filter=True,
        word_timestamps=True,
        condition_on_previous_text=False,
    )

    rows = []

    for segment in segments:
        words = list(segment.words or [])
        groups, current = [], []

        for word in words:
            text_length = sum(len(item.word) for item in current)

            if current and (
                text_length + len(word.word) > 42
                or word.end - current[0].start > 4.5
            ):
                groups.append(current)
                current = []

            current.append(word)

        if current:
            groups.append(current)

        entries = (
            [
                [
                    group[0].start,
                    group[-1].end,
                    "".join(item.word for item in group),
                ]
                for group in groups
            ]
            if groups
            else [[segment.start, segment.end, segment.text]]
        )

        for start, end, text in entries:
            text = plain_text(text)

            if traditional and info.language == "zh":
                text = CONVERTER.convert(text)

            if text and end > start:
                rows.append([start, end, text])

        emit(
            f"字幕辨識 / Transcribing: {segment.end:.1f}s · "
            f"{info.language}"
        )

    return rows


def generate_captions(job, config, emit, replace=False):
    if not job.get("clip"):
        raise ValueError(
            "請先準備影片 / Prepare a clip first."
        )

    if replace or not job.get("original_rows"):
        emit("產生原文字幕 / Generating original captions")

        job["original_rows"] = validate_rows(
            transcribe(
                job["clip"],
                job.get("source_language"),
                config["traditional"],
                emit,
            ),
            job["duration"],
        )
        save_job(job)

    if job.get("dub_video") and (
        replace or not job.get("translated_rows")
    ):
        emit("從配音辨識翻譯字幕 / Transcribing translated captions from dub")

        job["translated_rows"] = validate_rows(
            transcribe(
                job["dub_video"],
                job["target_language"],
                config["traditional"],
                emit,
            ),
            job["duration"],
        )
        save_job(job)


def import_srt(path):
    if not path:
        raise ValueError(
            "請上傳 SRT / Upload an SRT file."
        )

    path = Path(path)

    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(
            "SRT 上限為 2 MB / SRT limit is 2 MB."
        )

    content = path.read_text(encoding="utf-8-sig")

    return validate_rows([
        [
            item.start.total_seconds(),
            item.end.total_seconds(),
            item.content,
        ]
        for item in srt.parse(content)
    ])


def combine_rows(original, translated, mode, limit):
    if mode == "none":
        return []

    original = validate_rows(original, limit)
    translated = validate_rows(translated, limit)

    selected = {
        "original": original,
        "translated": translated,
        "bilingual": original + translated,
    }[mode]

    if not selected:
        raise ValueError(
            "所選模式沒有字幕 / No subtitles in the selected mode."
        )

    points = sorted({
        time_value
        for row in selected
        for time_value in row[:2]
    })

    output = []

    for start, end in zip(points, points[1:]):
        text = "\n".join(
            row[2] for row in selected
            if row[0] < end and row[1] > start
        )

        if not text:
            continue

        if output and output[-1][1] == start and output[-1][2] == text:
            output[-1][1] = end
        else:
            output.append([start, end, text])

    return output


# ------------------------------------------------------------
# Export
# ------------------------------------------------------------

def selected_video(job, audio_mode):
    path = job.get(
        "dub_video" if audio_mode == "dubbed" else "clip"
    )

    if not path or not Path(path).is_file():
        raise ValueError(
            "所選音訊尚未準備完成 / Selected audio is not available yet."
        )

    return path


def write_srt(path, rows):
    path.write_text(
        srt.compose([
            srt.Subtitle(
                index=index,
                start=timedelta(seconds=start),
                end=timedelta(seconds=end),
                content=text,
            )
            for index, (start, end, text) in enumerate(rows, 1)
        ]),
        encoding="utf-8",
    )


def vtt_time(seconds):
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}.{milliseconds:03}"


def ass_time(seconds):
    value = round(seconds * 100)
    hours, value = divmod(value, 360000)
    minutes, value = divmod(value, 6000)
    seconds, centiseconds = divmod(value, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"


def write_ass(path, rows, width, height, font_size):
    size = max(12, round(float(font_size) * min(width, height) / 720))
    margin = max(14, round(min(width, height) * 0.045))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans CJK TC,{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,{margin},{margin},{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = []

    for start, end, text in rows:
        text = (
            text.replace("\\", "＼")
            .replace("{", "｛")
            .replace("}", "｝")
            .replace("\n", r"\N")
        )

        lines.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},"
            f"Default,,0,0,0,,{text}"
        )

    path.write_text(header + "\n".join(lines), encoding="utf-8")


def export_video(job, config, emit):
    video = selected_video(job, config["audio_mode"])

    original = validate_rows(
        config["original_rows"], job["duration"]
    )
    translated = validate_rows(
        config["translated_rows"], job["duration"]
    )

    job["original_rows"] = original
    job["translated_rows"] = translated
    save_job(job)

    rows = combine_rows(
        original,
        translated,
        config["subtitle_mode"],
        job["duration"],
    )

    directory = (
        Path(job["directory"])
        / ("export_" + uuid.uuid4().hex[:10])
    )
    directory.mkdir()

    output = directory / "video.mp4"
    downloads = []

    for name, table in [
        ("original", original),
        ("translated", translated),
        ("selected", rows),
    ]:
        if table:
            path = directory / f"{name}.srt"
            write_srt(path, table)
            downloads.append(str(path))

    if rows:
        path = directory / "selected.vtt"
        blocks = [
            f"{vtt_time(start)} --> {vtt_time(end)}\n"
            f"{html.escape(text, quote=False)}"
            for start, end, text in rows
        ]
        path.write_text(
            "WEBVTT\n\n" + "\n\n".join(blocks) + "\n",
            encoding="utf-8",
        )
        downloads.append(str(path))

    edits = directory / "subtitle_edits.json"
    edits.write_text(
        json.dumps({
            "original": original,
            "translated": translated,
            "timeline": "prepared_clip_seconds",
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    downloads.append(str(edits))

    if not rows:
        emit("複製無字幕影片 / Copying video without subtitles")
        shutil.copyfile(video, output)

    elif config["export_mode"] == "soft":
        emit("快速匯出字幕軌 / Fast export with selectable subtitle track")

        ffmpeg([
            "-i", video,
            "-i", directory / "selected.srt",
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-map", "1:0",
            "-c:v", "copy",
            "-c:a", "copy",
            "-c:s", "mov_text",
            "-metadata:s:s:0", "language=und",
            "-metadata:s:s:0", "title=Subtitles",
            "-disposition:s:0", "default",
            "-movflags", "+faststart",
            output,
        ])

    else:
        emit("永久燒錄字幕 / Burning subtitles into video")

        stream = next(
            stream for stream in probe(video)["streams"]
            if stream["codec_type"] == "video"
        )

        write_ass(
            directory / "captions.ass",
            rows,
            stream["width"],
            stream["height"],
            config["font_size"],
        )

        ffmpeg([
            "-i", video,
            "-map", "0:v:0",
            "-map", "0:a:0",
            "-vf", "ass=captions.ass",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "24",
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            "-movflags", "+faststart",
            output,
        ], cwd=directory)

    probe(output)
    downloads.insert(0, str(output))
    return downloads


def execute(action, job, config, emit):
    if action == "prepare":
        prepare(job, config, emit)

        if config["auto_captions"]:
            generate_captions(job, config, emit)

    elif action == "dub":
        create_dub(job, config, emit)

        if config["auto_captions"]:
            generate_captions(job, config, emit)

    elif action == "resume":
        resume_dub(job, config, emit)

        if config["auto_captions"]:
            generate_captions(job, config, emit)

    elif action == "captions":
        generate_captions(job, config, emit, replace=True)

    elif action == "export":
        return export_video(job, config, emit)

    else:
        raise ValueError("未知操作 / Unknown action.")

    return None
