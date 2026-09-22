from __future__ import annotations

import argparse
import copy
import html
import os
import queue
import shutil
import threading
import time
from functools import partial

os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"

import gradio as gr

import engine as core
from youtube_urls import normalize_youtube_url


HEAVY_LOCK = threading.Lock()

CSS = (core.ROOT / "assets" / "style.css").read_text(
    encoding="utf-8"
)
CAPTION_JS = (core.ROOT / "assets" / "captions.js").read_text(
    encoding="utf-8"
)

EMPTY_QUOTA = """
<div class="quota-card">
  <div class="eyebrow">ELEVENLABS · 額度 / ALLOWANCE</div>
  <h3>尚未查詢 / Not checked</h3>
  <p>輸入 API 金鑰後按「更新額度」。<br>
  Enter your API key and click Refresh allowance.</p>
  <small>不會因為查詢額度而建立配音專案。<br>
  Checking allowance does not create a dubbing project.</small>
</div>
"""


def status_card(message, elapsed=0, running=False, failed=False):
    message = html.escape(str(message)).replace("\n", "<br>")

    if running:
        icon = '<div class="spinner"></div>'
        style = ""
        activity = '<div class="activity"><span></span></div>'
    else:
        icon = "<span>⚠️</span>" if failed else "<span>✅</span>"
        style = "failure" if failed else "success"
        activity = ""

    return f"""
    <div class="status-card {style}">
      <div class="status-line">
        {icon}
        <div>
          <b>{message}</b><br>
          <small>經過時間 / Elapsed: {elapsed:.0f}s</small>
        </div>
      </div>
      {activity}
    </div>
    """


def check_url_format(value):
    try:
        item = normalize_youtube_url(value)

        return (
            item.canonical,
            float(item.start_seconds),
            (
                '<div class="info-card">'
                f"<b>格式有效 / Valid format</b><p>{html.escape(item.kind)}</p>"
                "<small>已移除分享追蹤參數。這只是格式檢查，尚未確認可下載。<br>"
                "Sharing/tracking parameters removed. "
                "This checks format only, not download availability.</small>"
                "</div>"
            ),
        )
    except Exception as exc:
        return (
            "",
            0,
            '<div class="info-card">'
            + html.escape(str(exc))
            + "</div>",
        )


def verify_url(value):
    try:
        item, metadata = core.inspect_youtube(value)

        title = html.escape(str(metadata.get("title") or item.video_id))
        seconds = float(metadata.get("duration") or 0)

        return (
            item.canonical,
            float(item.start_seconds),
            (
                '<div class="info-card success">'
                "<b>目前可讀取影片資訊 / Metadata is currently accessible</b>"
                f"<p>{title}</p>"
                f"<p>長度 / Duration: {seconds:.1f}s</p>"
                "<small>這不保證之後的媒體下載一定成功。<br>"
                "This does not guarantee the subsequent media download.</small>"
                "</div>"
            ),
        )
    except Exception as exc:
        return (
            gr.skip(),
            gr.skip(),
            '<div class="info-card failure">'
            + html.escape(core.safe_error(exc)).replace("\n", "<br>")
            + "</div>",
        )


def import_table(path):
    try:
        return core.import_srt(path)
    except Exception as exc:
        raise gr.Error(
            "SRT 匯入失敗 / SRT import failed: " + str(exc)
        ) from None


def load_preview(job, audio_mode):
    try:
        return core.selected_video(job or {}, audio_mode)
    except Exception as exc:
        raise gr.Error(str(exc)) from None


def shift_table(rows, offset, job):
    try:
        rows = core.validate_rows(rows)
        offset = float(offset or 0)

        result = []

        for start, end, text in rows:
            start += offset
            end += offset

            if end <= 0:
                continue

            result.append([max(0, start), end, text])

        return core.validate_rows(
            result,
            (job or {}).get("duration"),
        )
    except Exception as exc:
        raise gr.Error(
            "調整時間失敗 / Timing shift failed: " + str(exc)
        ) from None


def build_app():
    with gr.Blocks(
        title="Video Dubber v2 · 影片配音",
        analytics_enabled=False,
        delete_cache=(3600, 86400),
    ) as app:
        gr.HTML("""
        <div class="hero">
          <div class="eyebrow">DIGIMARKETINGAI · VERSION 2</div>
          <h1>影片配音與字幕工作室</h1>
          <h1>Video Dubbing & Subtitle Studio</h1>
          <p>準備影片 → 建立配音 → 編輯字幕 → 匯出<br>
          Prepare → Dub → Edit captions → Export</p>
          <div class="badges">
            <span>YouTube + Shorts</span>
            <span>繁體中文 + English</span>
            <span>即時字幕 / Live captions</span>
            <span>額度查詢 / Allowance dashboard</span>
          </div>
        </div>
        """)

        state = gr.State({})

        with gr.Row():
            with gr.Column(scale=3):
                key = gr.Textbox(
                    label="ElevenLabs API 金鑰 / API key",
                    type="password",
                    placeholder="請使用您自己的金鑰 / Use your own API key",
                )

                refresh_quota = gr.Button(
                    "🔄 更新額度 / Refresh allowance"
                )

                gr.Markdown(
                    "金鑰會傳送至執行本工具的主機，再由主機呼叫 ElevenLabs。"
                    "僅在可信任的執行環境輸入金鑰。  \n"
                    "Your key is sent to this app's host to call ElevenLabs. "
                    "Use only a trusted instance."
                )

            with gr.Column(scale=4):
                quota = gr.HTML(EMPTY_QUOTA)

        with gr.Accordion(
            "① 影片來源 / Video source",
            open=True,
        ):
            input_mode = gr.Radio(
                choices=[
                    ("本機上傳 / Upload", "upload"),
                    ("YouTube / Shorts", "youtube"),
                ],
                value="upload",
                label="來源 / Source",
            )

            with gr.Row():
                upload = gr.File(
                    label="上傳影片 / Upload video · 500 MB",
                    file_types=[".mp4", ".mov", ".mkv", ".webm", ".avi"],
                    type="filepath",
                )

                with gr.Column():
                    url = gr.Textbox(
                        label="YouTube 網址 / URL",
                        placeholder=(
                            "youtube.com/watch?v=... / youtu.be/... / "
                            "youtube.com/shorts/..."
                        ),
                    )

                    with gr.Row():
                        format_button = gr.Button(
                            "🔗 整理網址 / Normalize URL"
                        )
                        verify_button = gr.Button(
                            "🌐 檢查影片 / Check availability"
                        )

                    normalized = gr.Textbox(
                        label="標準網址 / Canonical URL",
                        interactive=False,
                    )

                    suggested_start = gr.Number(
                        value=0,
                        label="網址內的開始秒數 / URL timestamp",
                        interactive=False,
                    )

                    use_timestamp = gr.Button(
                        "套用網址開始秒數 / Apply URL timestamp"
                    )

                    url_status = gr.HTML(
                        '<div class="info-card">'
                        "一般網址、分享網址及 Shorts 都可以。<br>"
                        "Regular, sharing, and Shorts links are accepted."
                        "</div>"
                    )

            with gr.Row():
                source = gr.Dropdown(
                    choices=[("自動偵測 / Auto detect", "auto")]
                    + core.LANGUAGES,
                    value="auto",
                    label="原始語言 / Source language",
                )

                start = gr.Number(
                    value=0,
                    minimum=0,
                    label="裁切開始秒數 / Clip start seconds",
                )

                length = gr.Slider(
                    5, 120,
                    value=20,
                    step=1,
                    label="片段長度（秒）/ Clip length",
                )

                quality = gr.Dropdown(
                    choices=[
                        ("較小 / Smaller · 480-class", "480"),
                        ("較清晰 / Clearer · 720-class", "720"),
                    ],
                    value="720",
                    label="輸出尺寸 / Output size",
                )

            gr.Markdown(
                "直向 Shorts 保留直向比例，不會強制拉成橫向。"
                "網址中的時間戳記只有按「套用」後才會改變裁切開始時間。  \n"
                "Portrait Shorts retain their aspect ratio. "
                "URL timestamps change the clip start only when you click Apply."
            )

            with gr.Row():
                auto_captions = gr.Checkbox(
                    value=True,
                    label="自動產生字幕 / Generate captions automatically",
                )
                traditional = gr.Checkbox(
                    value=True,
                    label="中文辨識轉繁體 / Convert recognized Chinese to Traditional",
                )

            permission = gr.Checkbox(
                value=False,
                label=(
                    "我有權處理此影片及聲音 / "
                    "I have permission to process this video and its voices"
                ),
            )

            prepare_button = gr.Button(
                "① 準備影片 / Prepare clip",
                variant="primary",
            )

        with gr.Accordion("② ElevenLabs 配音 / Dubbing", open=True):
            target = gr.Dropdown(
                choices=core.LANGUAGES,
                value="zh",
                label="配音語言 / Target language",
            )

            paid = gr.Checkbox(
                value=False,
                label=(
                    "我同意建立配音可能扣除額度或產生費用 / "
                    "I accept that creating a dub can consume allowance or incur charges"
                ),
            )

            with gr.Row():
                dub_button = gr.Button(
                    "② 建立付費配音 / Create paid dub",
                    variant="primary",
                )

                resume_button = gr.Button(
                    "繼續查詢既有配音 / Resume existing dub"
                )

            gr.Markdown(
                "**先準備影片，再建立配音。**"
                "繼續查詢不會建立新的付費專案。  \n"
                "**Prepare a clip before creating a dub.** "
                "Resume does not create a new paid project."
            )

        status = gr.HTML(
            status_card("準備就緒 / Ready")
        )

        project = gr.Textbox(
            label="專案 ID / Project ID",
            interactive=False,
        )

        activity_log = gr.Textbox(
            label="處理紀錄 / Activity log",
            lines=6,
            interactive=False,
        )

        clean_video = gr.File(
            label="未加字幕的影片 / Video without added captions"
        )

        gr.Markdown(
            "## ③ 即時字幕編輯 / Live subtitle editing\n"
            "**修改字幕不會改變配音語音。**"
            "按 Enter 或點選儲存格外完成編輯。  \n"
            "**Editing captions does not change spoken audio.** "
            "Press Enter or click outside a cell to commit an edit."
        )

        with gr.Row():
            audio_mode = gr.Radio(
                choices=core.AUDIO_CHOICES,
                value="original",
                label="預覽及匯出音訊 / Preview and export audio",
            )

            subtitle_mode = gr.Radio(
                choices=core.SUBTITLE_CHOICES,
                value="original",
                label="字幕模式 / Subtitle mode",
            )

        preview_button = gr.Button(
            "▶ 載入／切換預覽 / Load or switch preview"
        )

        preview = gr.Video(
            label="字幕預覽 / Caption preview",
            elem_id="editor-video",
            interactive=False,
        )

        gr.HTML(
            '<div id="caption-stats">'
            "原文 / Original: 0 · 翻譯 / Translated: 0"
            "</div>"
        )

        with gr.Tabs():
            with gr.Tab("原文 / Original"):
                original_rows = gr.Dataframe(
                    headers=[
                        "開始 / Start",
                        "結束 / End",
                        "文字 / Text",
                    ],
                    datatype=["number", "number", "str"],
                    type="array",
                    value=[],
                    row_count=(0, "dynamic"),
                    col_count=(3, "fixed"),
                    interactive=True,
                    label="原文字幕 / Original captions",
                )

                original_srt = gr.File(
                    label="匯入原文 UTF-8 SRT / Import original SRT",
                    file_types=[".srt"],
                    type="filepath",
                )

            with gr.Tab("翻譯 / Translated"):
                translated_rows = gr.Dataframe(
                    headers=[
                        "開始 / Start",
                        "結束 / End",
                        "文字 / Text",
                    ],
                    datatype=["number", "number", "str"],
                    type="array",
                    value=[],
                    row_count=(0, "dynamic"),
                    col_count=(3, "fixed"),
                    interactive=True,
                    label="翻譯字幕 / Translated captions",
                )

                translated_srt = gr.File(
                    label="匯入翻譯 UTF-8 SRT / Import translated SRT",
                    file_types=[".srt"],
                    type="filepath",
                )

        with gr.Row():
            shift_seconds = gr.Number(
                value=0,
                label="字幕位移秒數；可用負數 / Caption shift; negative allowed",
            )
            shift_original = gr.Button(
                "移動原文時間 / Shift original timings"
            )
            shift_translated = gr.Button(
                "移動翻譯時間 / Shift translated timings"
            )

        captions_button = gr.Button(
            "重新辨識字幕；取代目前表格 / Regenerate captions; replace tables"
        )

        gr.Markdown(
            "時間以**裁切後片段**為準。"
            "自動字幕可能有誤，請檢查。  \n"
            "Times are relative to the **prepared clip**. "
            "Review automatic captions for recognition and timing errors."
        )

        gr.Markdown("## ④ 匯出 / Export")

        with gr.Row():
            export_mode = gr.Radio(
                choices=[
                    ("可關閉字幕；較快 / Soft subtitles; faster", "soft"),
                    ("永久燒錄 / Burn in", "burn"),
                ],
                value="soft",
                label="匯出方式 / Export method",
            )

            font_size = gr.Slider(
                18, 54,
                value=32,
                step=1,
                label="燒錄字級 / Burn-in font size",
            )

        export_button = gr.Button(
            "④ 匯出 MP4 與字幕 / Export MP4 and captions",
            variant="primary",
        )

        downloads = gr.File(
            label="下載檔案 / Downloads",
            file_count="multiple",
        )

        gr.Markdown(
            "可關閉字幕需要播放器支援；永久燒錄會重新編碼影像。"
            "即時預覽的排版不保證與燒錄結果完全相同。  \n"
            "Soft subtitles require player support. Burn-in re-encodes video. "
            "Preview typography can differ from the burned-in output.\n\n"
            "**隱私提醒：**這不是強化隔離的多人服務。"
            "公開分享連結可能讓他人使用您的 CPU、磁碟及頻寬。"
            "處理敏感資料請使用可信任的本機環境。  \n"
            "**Privacy:** this is not a hardened multi-user service. "
            "A public share link exposes host resources. "
            "Use a trusted local instance for sensitive media."
        )

        fields = {
            "key": key,
            "input_mode": input_mode,
            "upload": upload,
            "url": url,
            "source": source,
            "start": start,
            "length": length,
            "quality": quality,
            "auto_captions": auto_captions,
            "traditional": traditional,
            "permission": permission,
            "target": target,
            "paid": paid,
            "audio_mode": audio_mode,
            "subtitle_mode": subtitle_mode,
            "export_mode": export_mode,
            "font_size": font_size,
            "original_rows": original_rows,
            "translated_rows": translated_rows,
        }

        action_buttons = [
            prepare_button,
            dub_button,
            resume_button,
            captions_button,
            export_button,
        ]

        helper_controls = [
            refresh_quota,
            format_button,
            verify_button,
            use_timestamp,
            preview_button,
            original_srt,
            translated_srt,
            shift_seconds,
            shift_original,
            shift_translated,
        ]

        editable = list(fields.values()) + action_buttons + helper_controls

        outputs = list(dict.fromkeys(
            editable + [
                state,
                status,
                project,
                activity_log,
                clean_video,
                downloads,
                preview,
                quota,
            ]
        ))

        def run_action(action, *values):
            config = dict(zip(fields, values[:-1]))
            config["key"] = str(config["key"] or "").strip()
            job = copy.deepcopy(values[-1] or {})

            job["original_rows"] = config["original_rows"] or []
            job["translated_rows"] = config["translated_rows"] or []

            events = queue.Queue()
            started = time.monotonic()
            logs = []
            latest = "開始處理 / Starting"

            def worker():
                acquired = HEAVY_LOCK.acquire(blocking=False)
                files = None
                failure = None
                refreshed_quota = None

                def emit(message):
                    events.put(("progress", str(message)))

                try:
                    if not acquired:
                        raise RuntimeError(
                            "另一項工作仍在執行 / Another task is still running."
                        )

                    files = core.execute(
                        action, job, config, emit
                    )

                except Exception as exc:
                    failure = core.safe_error(exc, config["key"])

                finally:
                    if acquired:
                        try:
                            core.save_job(job)
                        except Exception as exc:
                            failure = failure or core.safe_error(exc)

                        if action in {"dub", "resume"} and config["key"]:
                            emit("更新 ElevenLabs 額度 / Refreshing allowance")
                            refreshed_quota = core.quota_html(config["key"])

                        HEAVY_LOCK.release()

                    events.put((
                        "finished",
                        {
                            "error": failure,
                            "files": files,
                            "quota": refreshed_quota,
                        },
                    ))

            initial = {
                component: gr.update(interactive=False)
                for component in editable
            }

            initial.update({
                status: status_card(latest, running=True),
                activity_log: "",
                downloads: None,
            })

            yield initial

            threading.Thread(
                target=worker,
                daemon=True,
            ).start()

            while True:
                elapsed = time.monotonic() - started

                try:
                    kind, payload = events.get(timeout=1)
                except queue.Empty:
                    yield {
                        status: status_card(latest, elapsed, running=True)
                    }
                    continue

                if kind == "progress":
                    latest = core.safe_error(payload, config["key"])
                    logs.append(f"[{elapsed:6.1f}s] {latest}")

                    yield {
                        status: status_card(latest, elapsed, running=True),
                        activity_log: "\n".join(logs[-120:]),
                    }
                    continue

                failure = payload["error"]

                message = (
                    "處理失敗 / Processing failed:\n" + failure
                    if failure
                    else "完成 / Complete"
                )

                logs.append(f"[{elapsed:6.1f}s] {message}")

                result = {
                    component: gr.update(interactive=True)
                    for component in editable
                }

                result.update({
                    state: job,
                    status: status_card(
                        message,
                        elapsed,
                        failed=bool(failure),
                    ),
                    activity_log: "\n".join(logs[-120:]),
                    project: job.get("project_id", ""),
                    original_rows: gr.update(
                        value=job.get("original_rows", []),
                        interactive=True,
                    ),
                    translated_rows: gr.update(
                        value=job.get("translated_rows", []),
                        interactive=True,
                    ),
                    downloads: payload["files"],
                    clean_video: (
                        job.get("dub_video") or job.get("clip")
                    ),
                })

                if payload["quota"] is not None:
                    result[quota] = payload["quota"]

                # Do not reload video while merely exporting/recognizing.
                if action == "prepare":
                    result[audio_mode] = gr.update(
                        value="original", interactive=True
                    )
                    result[subtitle_mode] = gr.update(
                        value="original", interactive=True
                    )
                    result[preview] = job.get("clip")

                elif action in {"dub", "resume"} and job.get("dub_video"):
                    result[audio_mode] = gr.update(
                        value="dubbed", interactive=True
                    )
                    result[subtitle_mode] = gr.update(
                        value="translated", interactive=True
                    )
                    result[preview] = job["dub_video"]

                yield result
                break

        # Serialize all server-side work through the same queue group.
        event_options = {
            "concurrency_limit": 1,
            "concurrency_id": "studio",
            "api_visibility": "private",
        }

        for button, action in zip(
            action_buttons,
            ["prepare", "dub", "resume", "captions", "export"],
        ):
            button.click(
                fn=partial(run_action, action),
                inputs=list(fields.values()) + [state],
                outputs=outputs,
                trigger_mode="once",
                show_progress="minimal",
                **event_options,
            ).then(
                fn=None,
                inputs=[original_rows, translated_rows, subtitle_mode],
                outputs=[],
                js=CAPTION_JS,
                queue=False,
            )

        refresh_quota.click(
            fn=core.quota_html,
            inputs=key,
            outputs=quota,
            **event_options,
        )

        key.input(
            fn=lambda: EMPTY_QUOTA,
            inputs=[],
            outputs=quota,
            queue=False,
            api_visibility="private",
        )

        for button, function in [
            (format_button, check_url_format),
            (verify_button, verify_url),
        ]:
            button.click(
                fn=function,
                inputs=url,
                outputs=[normalized, suggested_start, url_status],
                **event_options,
            )

        use_timestamp.click(
            fn=lambda value: value,
            inputs=suggested_start,
            outputs=start,
            **event_options,
        )

        original_srt.upload(
            fn=import_table,
            inputs=original_srt,
            outputs=original_rows,
            **event_options,
        )

        translated_srt.upload(
            fn=import_table,
            inputs=translated_srt,
            outputs=translated_rows,
            **event_options,
        )

        shift_original.click(
            fn=shift_table,
            inputs=[original_rows, shift_seconds, state],
            outputs=original_rows,
            **event_options,
        )

        shift_translated.click(
            fn=shift_table,
            inputs=[translated_rows, shift_seconds, state],
            outputs=translated_rows,
            **event_options,
        )

        preview_button.click(
            fn=load_preview,
            inputs=[state, audio_mode],
            outputs=preview,
            **event_options,
        ).then(
            fn=None,
            inputs=[original_rows, translated_rows, subtitle_mode],
            outputs=[],
            js=CAPTION_JS,
            queue=False,
        )

        gr.on(
            triggers=[
                original_rows.change,
                translated_rows.change,
                subtitle_mode.change,
            ],
            fn=None,
            inputs=[original_rows, translated_rows, subtitle_mode],
            outputs=[],
            js=CAPTION_JS,
            queue=False,
        )

    return app


def main():
    parser = argparse.ArgumentParser(
        description="影片配音 v2 / Video Dubber v2"
    )

    parser.add_argument("--share", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7860)

    args = parser.parse_args()

    for executable in ("ffmpeg", "ffprobe"):
        if not shutil.which(executable):
            raise SystemExit(
                f"缺少 {executable} / Missing {executable}. "
                "Install FFmpeg and add it to PATH."
            )

    # Optional single-instance authentication.
    # This is not per-user storage isolation.
    user = os.getenv("APP_USER", "")
    password = os.getenv("APP_PASSWORD", "")

    if bool(user) != bool(password):
        raise SystemExit(
            "APP_USER 與 APP_PASSWORD 必須一起設定 / "
            "Set both APP_USER and APP_PASSWORD."
        )

    auth = (user, password) if user and password else None

    if args.share and auth is None:
        print(
            "警告：公開分享連結未設定登入。 / "
            "WARNING: public share link has no login."
        )

    app = build_app()
    app.queue(max_size=8)

    app.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        auth=auth,
        inbrowser=not args.share,
        inline=False,
        css=CSS,
        theme=gr.themes.Soft(),
        max_file_size="500mb",
        show_error=False,
        run_history=False,
        blocked_paths=[
            str(core.ROOT / ".git"),
            str(core.ROOT / ".env"),
            str(core.ROOT / ".venv"),
        ],
    )


if __name__ == "__main__":
    main()
