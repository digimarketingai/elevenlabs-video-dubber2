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
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.parse import urlsplit

import requests
import srt
from opencc import OpenCC
from requests.adapters import HTTPAdapter

from youtube_urls import normalize_youtube_url


ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"
WORK.mkdir(exist_ok=True)

API = "https://api.elevenlabs.io/v1"

MAX_SOURCE_BYTES = 500 * 1024 * 1024
MAX_YOUTUBE_SECONDS = 20 * 60
MAX_CAPTION_ROWS = 2000

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


# ============================================================
# Common helpers
# ============================================================

def safe_error(error, key=""):
    """Redact credentials and URLs before displaying an error."""
    text = str(error)

    if key:
        text = text.replace(str(key), "[REDACTED]")
        stripped = str(key).strip().strip("\"'")
        if stripped:
            text = text.replace(stripped, "[REDACTED]")

    text = re.sub(
        r"(?i)\bsk_[A-Za-z0-9_-]+\b",
        "[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)(xi-api-key|authorization)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        text,
    )
    text = re.sub(r"https?://\S+", "[URL]", text)
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text[-2400:]


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return "not installed"


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
    except FileNotFoundError:
        raise RuntimeError(
            f"找不到執行工具 / Executable not found: {args[0]}"
        ) from None

    if result.returncode:
        detail = safe_error(result.stderr or result.stdout)
        raise RuntimeError(
            f"工具執行失敗 / Tool exited with code "
            f"{result.returncode}:\n{detail}"
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
    data = probe(path)

    try:
        value = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(
            "無法讀取媒體長度 / Cannot determine media duration."
        ) from None

    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            "無效媒體長度 / Invalid media duration."
        )

    return value


def save_job(job):
    if not job.get("directory"):
        return

    destination = Path(job["directory"]) / "job.json"
    temporary = destination.with_suffix(".tmp")

    # The API key is never added to the job dictionary by this module.
    temporary.write_text(
        json.dumps(job, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(destination)


# ============================================================
# YouTube environment and diagnostics
# ============================================================

def find_deno():
    executable = shutil.which("deno")
    if executable:
        return executable

    locations = [
        Path.home() / ".deno" / "bin" / "deno",
    ]

    configured = os.environ.get("DENO_INSTALL", "").strip()
    if configured:
        locations.insert(
            0,
            Path(configured).expanduser() / "bin" / "deno",
        )

    for candidate in locations:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    return None


def ytdlp_base():
    if package_version("yt-dlp") == "not installed":
        raise RuntimeError(
            "目前 Python 環境未安裝 yt-dlp。 / "
            "yt-dlp is not installed in the app's Python environment."
        )

    args = [
        sys.executable,
        "-m", "yt_dlp",
        "--ignore-config",
        "--no-playlist",
        "--no-progress",
        "--no-colors",
        "--socket-timeout", "25",
        "--retries", "2",
        "--fragment-retries", "2",
    ]

    deno = find_deno()
    node = shutil.which("node")

    if deno:
        args += ["--js-runtimes", f"deno:{deno}"]
    elif node:
        args += ["--js-runtimes", f"node:{node}"]
    else:
        raise RuntimeError(
            "找不到 Deno 或 Node JavaScript runtime。"
            "請重新執行 colab.sh 安裝 Deno。 / "
            "No Deno or Node runtime found. "
            "Run colab.sh to install Deno."
        )

    return args


def youtube_error_message(error, video_id, stage):
    detail = safe_error(error)
    lower = detail.lower()

    if (
        "sign in" in lower
        or "not a bot" in lower
        or "login required" in lower
    ):
        reason = (
            "服務要求登入或驗證，或目前主機受到存取限制。 / "
            "The response requests sign-in/verification or indicates "
            "an access restriction."
        )
    elif (
        "private video" in lower
        or "members-only" in lower
        or "members only" in lower
    ):
        reason = (
            "回應指出影片有私人或會員存取限制。 / "
            "The response indicates private or members-only access."
        )
    elif "not available in your country" in lower:
        reason = (
            "回應指出目前執行主機所在區域無法存取。 / "
            "The response indicates a regional restriction."
        )
    elif "video unavailable" in lower:
        reason = (
            "YouTube 對目前執行環境回傳 Video unavailable。"
            "僅憑這段訊息無法判定影片已刪除；"
            "也可能與存取限制或下載器相容性有關。 / "
            "YouTube returned Video unavailable to this environment. "
            "This alone does not prove deletion; access restrictions "
            "or extractor compatibility may also be involved."
        )
    elif "429" in lower or "too many requests" in lower:
        reason = (
            "回應指出請求過多，請停止重複嘗試並稍後再試。 / "
            "The response indicates rate limiting. "
            "Stop repeated attempts and try later."
        )
    elif "403" in lower:
        reason = (
            "伺服器拒絕目前請求。 / "
            "The server refused this request."
        )
    elif (
        "javascript" in lower
        or "challenge" in lower
        or "ejs" in lower
    ):
        reason = (
            "請檢查 yt-dlp、EJS 套件及 JavaScript runtime。 / "
            "Check yt-dlp, its EJS package, and the JavaScript runtime."
        )
    else:
        reason = (
            "目前無法完成 YouTube 操作；請參考下方診斷。 / "
            "The YouTube operation failed; see the diagnostic below."
        )

    return (
        f"YouTube {stage}失敗 / YouTube operation failed\n"
        f"影片 ID / Video ID: {video_id}\n"
        f"yt-dlp: {package_version('yt-dlp')}\n"
        f"yt-dlp-ejs: {package_version('yt-dlp-ejs')}\n\n"
        f"{reason}\n\n"
        "建議 / Next steps:\n"
        "1. 在一般瀏覽器確認此影片是否可以播放。\n"
        "   Check playback in your normal browser.\n"
        "2. 更新本工具 .venv 內的 yt-dlp[default]。\n"
        "   Update yt-dlp[default] inside this app's .venv.\n"
        "3. 若瀏覽器可播但 Colab 仍失敗，請使用您有權使用的"
        "本機影片，或在自己的電腦執行工具。\n"
        "   If browser playback works but Colab fails, upload an "
        "authorized local file or run the app on your own computer.\n\n"
        "診斷 / Diagnostic:\n"
        + detail[-1200:]
    )


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
            youtube_error_message(
                exc, normalized.video_id, "可用性檢查"
            )
        ) from None

    if not isinstance(metadata, dict):
        raise RuntimeError(
            "YouTube 回傳無效影片資訊 / Invalid YouTube metadata."
        )

    try:
        seconds = float(metadata.get("duration") or 0)
    except (TypeError, ValueError):
        seconds = 0

    if (
        metadata.get("is_live")
        or metadata.get("live_status") in {"is_live", "is_upcoming"}
    ):
        raise ValueError(
            "不接受正在直播或尚未開始的直播。 / "
            "Active or upcoming live streams are not supported."
        )

    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(
            "無法確認影片長度。 / Cannot determine video duration."
        )

    if seconds > MAX_YOUTUBE_SECONDS:
        raise ValueError(
            "YouTube 來源影片不可超過 20 分鐘。 / "
            "YouTube source videos must be at most 20 minutes."
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
                "--max-filesize", "250M",
                "-o", str(directory / "source.%(ext)s"),
                normalized.canonical,
            ],
            timeout=1200,
        )
    except Exception as exc:
        raise RuntimeError(
            youtube_error_message(
                exc, normalized.video_id, "下載"
            )
        ) from None

    candidates = [
        path for path in directory.glob("source.*")
        if path.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
        and path.is_file()
    ]

    if not candidates:
        raise RuntimeError(
            "未取得影片檔案，下載可能因大小限制而跳過。 / "
            "No video file was produced. "
            "The download may have been skipped by the size limit."
        )

    source = max(candidates, key=lambda path: path.stat().st_size)

    return source, {
        "youtube_url": normalized.canonical,
        "title": str(metadata.get("title") or ""),
    }


# ============================================================
# ElevenLabs authentication and safe error reporting
# ============================================================

class ElevenLabsAPIError(RuntimeError):
    def __init__(self, http_status, provider_status, message):
        self.http_status = http_status
        self.provider_status = provider_status
        super().__init__(message)


def normalize_api_key(value):
    key = str(value or "").strip()

    # Accept a key accidentally wrapped in matching ASCII quotes.
    if (
        len(key) >= 2
        and key[0] == key[-1]
        and key[0] in {"'", '"'}
    ):
        key = key[1:-1].strip()

    if not key:
        raise ValueError(
            "請輸入 ElevenLabs API 金鑰。 / "
            "Enter your ElevenLabs API key."
        )

    if key.lower().startswith(("bearer ", "xi-api-key:")):
        raise ValueError(
            "請只貼上金鑰本身，不要包含 Bearer 或 xi-api-key:。 / "
            "Paste only the key, without Bearer or xi-api-key:."
        )

    if (
        any(char.isspace() for char in key)
        or not key.isascii()
        or any(ord(char) < 33 or ord(char) > 126 for char in key)
        or "*" in key
        or "..." in key
    ):
        raise ValueError(
            "金鑰含空白、非 ASCII 字元或遮蔽符號。"
            "請重新複製完整金鑰，不要貼上遮蔽後的顯示文字。 / "
            "The key contains whitespace, non-ASCII characters, "
            "or masking symbols. Copy the complete secret key."
        )

    # Do not enforce a particular prefix or fixed key length.
    return key


def api_session(key):
    key = normalize_api_key(key)

    session = requests.Session()
    session.headers.update({
        "xi-api-key": key,
        "Accept": "application/json",
    })

    # Disable transport retries for every method.
    # api_json explicitly retries selected GET failures only.
    session.mount("https://", HTTPAdapter(max_retries=0))
    session.mount("http://", HTTPAdapter(max_retries=0))

    return session


def extract_provider_error(response, key):
    """Read selected JSON fields, never expose the entire response body."""
    code = ""
    message = ""

    try:
        data = response.json()
    except ValueError:
        data = None

    if isinstance(data, dict):
        detail = data.get("detail", data)

        if isinstance(detail, dict):
            raw_code = detail.get("status") or detail.get("code") or ""
            raw_message = detail.get("message") or ""

            if isinstance(raw_code, str):
                code = raw_code

            if isinstance(raw_message, str):
                message = raw_message

        elif isinstance(detail, str):
            message = detail

    code = safe_error(code, key)[:120]
    message = safe_error(message, key)[:800]

    return code, message


def api_error_hint(http_status, provider_status):
    code = provider_status.lower()

    if "permission" in code or "scope" in code:
        return (
            "此金鑰缺少所呼叫端點的權限。"
            "請在 ElevenLabs API Keys 設定檢查對應權限；"
            "訂閱讀取與配音權限可能不同。 / "
            "This key lacks permission for the requested endpoint. "
            "Check its API-key scopes; subscription and dubbing "
            "permissions may differ."
        )

    if "quota" in code or "credit" in code:
        return (
            "服務回報額度或 credit 限制。"
            "請檢查工作區額度、金鑰額度限制及帳戶設定。 / "
            "The service reports a quota or credit restriction. "
            "Check workspace usage, key limits, and account settings."
        )

    if http_status == 401:
        return (
            "此請求未通過驗證。請重新複製完整有效的 ElevenLabs "
            "API 金鑰，確認未停用或撤銷，並檢查下方服務訊息。"
            "401 本身不能證明額度不足。 / "
            "Authentication was rejected. Copy a complete active "
            "ElevenLabs API key and inspect the provider message. "
            "HTTP 401 alone does not establish exhausted allowance."
        )

    if http_status == 403:
        return (
            "請求被拒絕。請檢查服務訊息、端點權限、"
            "工作區政策及金鑰 IP 限制。 / "
            "Access was denied. Check the provider message, scopes, "
            "workspace policy, and API-key IP restrictions."
        )

    if http_status == 429:
        return (
            "請求受到限流。請稍後再試。 / "
            "The request was rate limited. Try again later."
        )

    if http_status in {400, 422}:
        return (
            "服務拒絕請求參數，請檢查服務訊息。 / "
            "The service rejected the request parameters."
        )

    if http_status == 404:
        return (
            "找不到端點或資源，或目前金鑰無法存取該資源。 / "
            "The endpoint/resource was not found or is inaccessible."
        )

    if http_status >= 500:
        return (
            "服務端錯誤；若為建立專案請求，"
            "請先檢查帳戶再決定是否重試。 / "
            "Server error. For project creation, check your account "
            "before deciding whether to try again."
        )

    return (
        "請參考服務訊息；不要反覆建立付費專案。 / "
        "Review the provider message; do not repeatedly create paid jobs."
    )


def api_json(session, method, path, **kwargs):
    method = method.upper()
    timeout = kwargs.pop("timeout", (20, 180))
    key = session.headers.get("xi-api-key", "")

    # Three total attempts for GET; exactly one for POST and others.
    attempts = 3 if method == "GET" else 1
    retry_statuses = {429, 500, 502, 503, 504}

    for attempt in range(attempts):
        try:
            response = session.request(
                method,
                API + path,
                timeout=timeout,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException:
            if method == "GET" and attempt + 1 < attempts:
                time.sleep(1 + attempt)
                continue

            message = (
                "ElevenLabs 連線失敗或逾時。 / "
                "ElevenLabs connection failed or timed out."
            )

            if method == "POST":
                message += (
                    "\n建立結果可能不明；伺服器可能已接受請求。"
                    "本工具沒有自動重送。請先檢查 ElevenLabs 帳戶。 / "
                    "The server may already have accepted the request. "
                    "No automatic retry was made. Check ElevenLabs first."
                )

            raise RuntimeError(message) from None

        status = response.status_code

        if (
            method == "GET"
            and status in retry_statuses
            and attempt + 1 < attempts
        ):
            # Respect short numeric Retry-After values.
            # For longer/date-formatted instructions, return the error
            # rather than retrying earlier than instructed.
            header = response.headers.get("Retry-After", "")
            delay = float(1 + attempt)

            if header:
                try:
                    requested_delay = float(header)
                except ValueError:
                    requested_delay = None

                if (
                    requested_delay is None
                    or not math.isfinite(requested_delay)
                    or requested_delay < 0
                    or requested_delay > 15
                ):
                    can_retry = False
                else:
                    can_retry = True
                    delay = max(delay, requested_delay)
            else:
                can_retry = True

            if can_retry:
                response.close()
                time.sleep(delay)
                continue

        if not 200 <= status < 300:
            provider_status, provider_message = extract_provider_error(
                response, key
            )
            response.close()

            parts = [
                f"ElevenLabs HTTP {status}",
                f"端點 / Endpoint: {method} {path}",
                api_error_hint(status, provider_status),
            ]

            if provider_status:
                parts.append(
                    "服務錯誤代碼 / Provider status: " + provider_status
                )

            if provider_message:
                parts.append(
                    "服務訊息 / Provider message: " + provider_message
                )

            if method == "POST":
                parts.append(
                    "此 POST 未自動重送。 / This POST was not retried."
                )

            raise ElevenLabsAPIError(
                status,
                provider_status,
                "\n".join(parts),
            )

        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(
                "ElevenLabs 回傳非 JSON 資料。 / "
                "ElevenLabs returned invalid JSON."
            ) from None
        finally:
            response.close()

        if not isinstance(data, dict):
            raise RuntimeError(
                "ElevenLabs 回傳結構與預期不同。 / "
                "Unexpected ElevenLabs response structure."
            )

        return data

    raise RuntimeError("Request did not complete.")


def quota_html(key):
    try:
        clean_key = normalize_api_key(key)

        with api_session(clean_key) as session:
            data = api_json(
                session,
                "GET",
                "/user/subscription",
                timeout=(15, 30),
            )

        used = int(data["character_count"])
        limit = int(data["character_limit"])

        if used < 0 or limit < 0:
            raise ValueError(
                "服務回傳無效額度 / Invalid allowance response."
            )

        remaining = max(0, limit - used)
        percent = min(100, used / limit * 100) if limit else 0

        tier = html.escape(str(data.get("tier", "—")))
        status = html.escape(str(data.get("status", "—")))
        extension = html.escape(
            str(data.get("max_credit_limit_extension", "Not provided"))
        )

        refreshed = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

        reset_text = "未提供 / Not provided"
        reset = data.get("next_character_count_reset_unix")

        if reset is not None:
            try:
                reset_text = datetime.fromtimestamp(
                    float(reset), timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S UTC")
            except (TypeError, ValueError, OverflowError, OSError):
                pass

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
          <p>
            帳戶狀態 / Account status: {status}<br>
            額外用量上限設定 / Overage cap setting: {extension}<br>
            下次重設 / Next reset: {reset_text}
          </p>
          <small>
            更新 / Updated: {refreshed}<br>
            依 character_count / character_limit 計算，
            不是 LLM Token、現金餘額或保證可花費的總額。<br>
            Based on character_count / character_limit,
            not LLM tokens, cash, or a guaranteed spending balance.
          </small>
        </div>
        """

    except Exception as exc:
        message = html.escape(
            safe_error(exc, str(key or ""))
        ).replace("\n", "<br>")

        return f"""
        <div class="quota-card failure">
          <b>無法讀取額度 / Allowance unavailable</b>
          <p>{message}</p>
          <small>
            查詢失敗不會顯示假餘額，也不會建立配音專案。<br>
            A failed lookup does not invent a balance
            or create a dubbing project.<br>
            請勿將 API 金鑰貼到公開訊息或截圖。<br>
            Never share your API key in messages or screenshots.
          </small>
        </div>
        """


def fetch_audio(url, output):
    parsed = urlsplit(url)

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError(
            "無效音訊下載網址 / Invalid audio download URL."
        )

    # Separate session: never send xi-api-key to storage URLs.
    try:
        with requests.get(
            url,
            stream=True,
            timeout=(20, 180),
            allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise RuntimeError(
                    f"音訊下載 HTTP {response.status_code}。"
                    "請按繼續查詢取得新的下載資訊。 / "
                    "Audio download failed. Use Resume."
                )

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


# ============================================================
# Media preparation
# ============================================================

def prepare(job, config, emit):
    if not config["permission"]:
        raise ValueError(
            "請先確認內容及聲音授權 / Confirm content and voice permission."
        )

    start = float(config["start"] or 0)
    length = float(config["length"] or 20)

    if not math.isfinite(start) or start < 0:
        raise ValueError("開始時間無效 / Invalid start time.")

    if not math.isfinite(length) or not 5 <= length <= 120:
        raise ValueError(
            "片段長度須為 5–120 秒 / Clip length must be 5–120 seconds."
        )

    directory = Path(tempfile.mkdtemp(prefix="v2_", dir=WORK))

    # Keep the previous job if downloading/preparing the new one fails.
    prepared = {
        "directory": str(directory),
        "original_rows": [],
        "translated_rows": [],
    }

    if config["input_mode"] == "youtube":
        source, metadata = download_youtube(
            config["url"], directory, emit
        )
        prepared.update(metadata)
    else:
        if not config["upload"]:
            raise ValueError("請先上傳影片 / Upload a video first.")

        source = Path(config["upload"])
        prepared["title"] = source.name

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

    emit("準備影片 / Preparing clip")

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

    prepared.update({
        "clip": str(clip),
        "source_audio": str(audio),
        "duration": duration(clip),
        "source_language": (
            None if config["source"] == "auto"
            else config["source"]
        ),
    })

    save_job(prepared)
    job.clear()
    job.update(prepared)

    if config["input_mode"] == "youtube":
        for path in directory.glob("source.*"):
            if path.is_file():
                path.unlink(missing_ok=True)


# ============================================================
# Dubbing
# ============================================================

def create_dub(job, config, emit):
    if not job.get("clip"):
        raise ValueError("請先準備影片 / Prepare a clip first.")

    if job.get("project_id") or job.get("creation_uncertain"):
        raise ValueError(
            "已有專案或建立結果不明。請使用繼續查詢，"
            "或先在 ElevenLabs 確認後再準備新片段。 / "
            "A project exists or creation is uncertain. "
            "Use Resume or check ElevenLabs before preparing another clip."
        )

    if not config["permission"] or not config["paid"]:
        raise ValueError(
            "請確認內容授權及付費配音 / Confirm permission and paid dubbing."
        )

    target = config["target"]

    if target not in {code for _, code in LANGUAGES}:
        raise ValueError("無效目標語言 / Invalid target language.")

    if target == job.get("source_language"):
        raise ValueError(
            "來源與目標語言不可相同 / Source and target languages must differ."
        )

    # Validate locally before setting the creation-uncertain marker.
    key = normalize_api_key(config["key"])

    with api_session(key) as session:
        data = {
            "reference": "digimarketingai Video Dubber v2",
            "model_id": "dubbing_v2",
            "target_language": target,
        }

        if job.get("source_language"):
            data["source_language"] = job["source_language"]

        with open(job["source_audio"], "rb") as file:
            job["target_language"] = target
            job["creation_uncertain"] = True
            save_job(job)

            emit("建立付費配音專案 / Creating paid dubbing project")

            try:
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
            except ElevenLabsAPIError as exc:
                # Explicit client-side rejection: permit a later manual
                # attempt after the user fixes the issue.
                # Never automatically retry the POST.
                if exc.http_status in {400, 401, 403, 404, 422, 429}:
                    job["creation_uncertain"] = False
                    save_job(job)
                raise

        project_id = result.get("project_id")

        if not isinstance(project_id, str) or not project_id:
            raise RuntimeError(
                "建立回應沒有有效 project_id；建立結果不明。"
                "請先檢查 ElevenLabs 帳戶。 / "
                "Creation returned no valid project_id. "
                "Check ElevenLabs before creating another project."
            )

        job["project_id"] = project_id
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
            "若建立請求逾時且沒有 ID，請先檢查 ElevenLabs。 / "
            "No resumable project in this session. "
            "If creation timed out without an ID, check ElevenLabs."
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
                    "The dubbing project failed. Check ElevenLabs."
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
                "已查詢約 30 分鐘；雲端可能仍在處理。"
                "請按繼續查詢。 / "
                "Polling stopped after about 30 minutes. Use Resume."
            )

    url = ((target_data or {}).get("outputs") or {}).get("lossless_audio")

    if not isinstance(url, str) or not url:
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
            "配音長度與影片不同；將裁切或補靜音。 / "
            "Dub duration differs; audio will be trimmed or padded."
        )

    emit("合併影片與配音 / Assembling dubbed video")

    ffmpeg([
        "-i", job["clip"],
        "-protocol_whitelist", "file,pipe",
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


# ============================================================
# Subtitle recognition and validation
# ============================================================

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

        if len(output) > MAX_CAPTION_ROWS:
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
        raise ValueError("請先準備影片 / Prepare a clip first.")

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
        emit("從配音辨識翻譯字幕 / Transcribing captions from dub")

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
        raise ValueError("請上傳 SRT / Upload an SRT file.")

    path = Path(path)

    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("SRT 上限為 2 MB / SRT limit is 2 MB.")

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

    choices = {
        "original": original,
        "translated": translated,
        "bilingual": original + translated,
    }

    if mode not in choices:
        raise ValueError("無效字幕模式 / Invalid subtitle mode.")

    selected = choices[mode]

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


# ============================================================
# Export
# ============================================================

def selected_video(job, audio_mode):
    if audio_mode not in {"dubbed", "original"}:
        raise ValueError("無效音訊模式 / Invalid audio mode.")

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
    font_size = float(font_size)

    if not math.isfinite(font_size) or not 18 <= font_size <= 54:
        raise ValueError("無效字幕字級 / Invalid subtitle font size.")

    size = max(12, round(font_size * min(width, height) / 720))
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
        emit("快速匯出字幕軌 / Exporting selectable subtitle track")

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

    elif config["export_mode"] == "burn":
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

    else:
        raise ValueError("無效匯出模式 / Invalid export mode.")

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
