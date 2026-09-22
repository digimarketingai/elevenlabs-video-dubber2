# 🎬 ElevenLabs Video Dubber v2
# 影片配音與即時字幕編輯器 v2

Community project, not an official ElevenLabs application.  
社群專案，非 ElevenLabs 官方應用程式。

## Features / 功能

- English and Traditional Chinese interface.  
  英文與繁體中文介面。
- Upload a local video or import a supported YouTube video.  
  上傳本機影片或匯入支援的 YouTube 影片。
- Accept regular YouTube links, sharing links, youtu.be links,
  Shorts, mobile links, and embedded-video links.  
  支援一般、分享、短網址、Shorts、行動版及嵌入影片網址。
- Normalize links and separately check online availability.  
  網址格式整理與線上可用性分開檢查。
- Preserve portrait and landscape aspect ratios.  
  保留直向及橫向影片比例。
- ElevenLabs subscription allowance dashboard.  
  ElevenLabs 訂閱額度面板。
- Project-based ElevenLabs Dubbing v2 integration.  
  使用專案式 ElevenLabs Dubbing v2 API。
- Original, translated, bilingual, or no subtitles.  
  原文、翻譯、雙語或無字幕。
- Editable subtitle text and timings.  
  可編輯字幕文字及時間。
- Shift subtitle timings forward or backward.  
  可將字幕時間向前或向後移動。
- UTF-8 SRT import.  
  匯入 UTF-8 SRT。
- Export MP4, SRT, VTT, and subtitle-edit JSON.  
  匯出 MP4、SRT、VTT 及字幕編輯 JSON。
- Animated status cards and elapsed-time display.  
  動態狀態卡與經過時間顯示。
- Optional single-instance login.  
  可選擇設定整個應用程式的登入帳密。

The default target language is Chinese / 中文.  
預設配音語言為中文。

## Repository layout / 檔案結構

```text
app.py
engine.py
youtube_urls.py
requirements.txt
colab.sh
README.md
.gitignore
assets/
  style.css
  captions.js
tests/
  test_urls.py
```

## Quick start in Google Colab / Google Colab 快速開始

First upload all v2 files to the repository.  
請先將全部 v2 檔案上傳至儲存庫。

In a fresh Colab runtime, run:  
在全新的 Colab 執行階段執行：

```python
!git clone https://github.com/digimarketingai/elevenlabs-video-dubber2.git /content/video-dubber
!bash /content/video-dubber/colab.sh
```

Open the printed Gradio share link.  
開啟輸出的 Gradio 分享連結。

Keep the cell running and download your files before ending the runtime.  
請保持儲存格執行，並在結束執行階段前下載檔案。

### Restart / 重新啟動

Stop the running app cell first.  
請先停止正在執行的應用程式儲存格。

```python
!bash /content/video-dubber/colab.sh
```

### Update / 更新

After committing the new files to GitHub, stop the running app and run:  
將新版檔案提交至 GitHub 後，停止應用程式再執行：

```python
!git -C /content/video-dubber pull --ff-only
!bash /content/video-dubber/colab.sh
```

If Git reports local changes, resolve them before pulling.  
如果 Git 顯示本機檔案已修改，請先處理修改或衝突。

Do not repeatedly clone into an existing nonempty folder.  
不要重複 clone 到已存在且非空白的資料夾。

## Local installation / 本機安裝

Use Python 3.10 or newer. Python 3.11 or 3.12 is a reasonable
starting environment for this project.  
請使用 Python 3.10 以上版本；本專案可先以 Python 3.11 或 3.12 建立環境。

Required system tools:  
系統工具需求：

- FFmpeg and FFprobe on PATH.
- FFmpeg with libass support for subtitle burn-in.
- A suitable CJK font for Chinese captions.
- Deno for the configured YouTube workflow.

### Ubuntu / Debian

```bash
sudo apt-get update
sudo apt-get install -y \
  ffmpeg \
  fonts-noto-cjk \
  fonts-noto-core \
  python3-venv \
  curl \
  unzip
```

Install Deno using its official installer if needed:  
如尚未安裝 Deno，可使用官方安裝程式：

```bash
curl -fsSL https://deno.land/install.sh -o /tmp/install-deno.sh
sh /tmp/install-deno.sh -y
export PATH="$HOME/.deno/bin:$PATH"
```

Clone and install:  
下載並安裝：

```bash
git clone https://github.com/digimarketingai/elevenlabs-video-dubber2.git
cd elevenlabs-video-dubber2

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m unittest discover -s tests -v
python app.py
```

Open:  
開啟：

```text
http://127.0.0.1:7860
```

For Windows, install FFmpeg and Deno separately and add them to PATH.
Activate the environment with:  
Windows 請另外安裝 FFmpeg 與 Deno，並加入 PATH。
虛擬環境啟用指令：

```powershell
.venv\Scripts\Activate.ps1
```

### Public share link / 公開分享連結

```bash
python app.py --share
```

Without optional authentication, anyone with the share link can use
the application.  
未設定選用登入功能時，持有分享連結的人都可能使用此工具。

### Optional authentication / 選用登入功能

Set both environment variables before launching:  
啟動前請同時設定以下環境變數：

```bash
export APP_USER="your-username"
export APP_PASSWORD="use-a-long-unique-password"
python app.py --share
```

This is one application-level login, not individual account isolation.  
這是整個應用程式的登入，不是個別使用者隔離系統。

The app does not automatically read a .env file.  
本工具不會自動讀取 .env 檔案。

## Workflow / 操作流程

1. Upload a video or paste a YouTube URL.  
   上傳影片或貼上 YouTube 網址。
2. For YouTube, click Normalize URL, then optionally Check availability.  
   YouTube 可先整理網址，再檢查影片可用性。
3. If you want to use the timestamp inside the URL, click Apply URL timestamp.  
   如要使用網址內的開始時間，請按套用網址開始秒數。
4. Choose source language, clip start, clip duration, and output size.  
   選擇原始語言、開始秒數、長度及尺寸。
5. Confirm content and voice permission.  
   確認內容及聲音授權。
6. Click Prepare clip.  
   按準備影片。
7. Enter your ElevenLabs key and optionally refresh allowance.  
   輸入 ElevenLabs 金鑰，並可更新額度。
8. Choose a target language and accept paid dubbing.  
   選擇配音語言並確認付費配音。
9. Click Create paid dub.  
   按建立付費配音。
10. Edit subtitles after processing finishes.  
    處理完成後編輯字幕。
11. Select audio, subtitle mode, and export method.  
    選擇音訊、字幕模式及匯出方式。
12. Click Export and download the files.  
    按匯出並下載檔案。

Preparing a new clip replaces the current in-browser job. Export anything
you want to keep before starting another clip.  
準備新片段會取代目前瀏覽器中的工作。開始新片段前請先匯出需要保留的內容。

## YouTube URL support / YouTube 網址支援

Supported example forms:  
支援的網址格式範例：

```text
https://www.youtube.com/watch?v=VIDEO_ID
https://www.youtube.com/watch?v=VIDEO_ID&feature=shared
https://youtu.be/VIDEO_ID?si=SHARE_PARAMETER
https://www.youtube.com/shorts/VIDEO_ID
https://m.youtube.com/watch?v=VIDEO_ID
https://www.youtube.com/embed/VIDEO_ID
https://www.youtube-nocookie.com/embed/VIDEO_ID
```

The parser requires an 11-character video ID.  
網址解析器要求有效的 11 字元影片 ID。

Sharing parameters such as `si` and `feature` are removed from the
canonical URL.  
標準網址會移除 `si`、`feature` 等分享參數。

A watch URL containing a playlist parameter is treated as one video.  
包含播放清單參數的一般影片網址只會處理該支影片。

Playlist-only URLs, channel URLs, arbitrary redirect URLs, and
non-YouTube domains are rejected.  
不接受純播放清單、頻道、任意重新導向網址及非 YouTube 網域。

Recorded videos using the `/live/` URL form can be recognized,
but active or upcoming live streams are rejected.  
可辨識使用 `/live/` 格式的已錄製影片，但不接受正在直播或尚未開始的直播。

### Format validation is not download permission
### 格式有效不等於可以下載

YouTube availability depends on the video, network, yt-dlp behavior,
JavaScript runtime, and access restrictions.  
可用性會受到影片、網路、yt-dlp、JavaScript 執行環境及存取限制影響。

The application does not bypass DRM, sign-in restrictions,
age restrictions, or other access controls.  
本工具不會繞過 DRM、登入、年齡或其他存取限制。

There is no cookie-upload feature in this version.  
本版本不提供 Cookie 上傳功能。

If importing fails, upload an authorized local video instead.  
匯入失敗時，請上傳您有權使用的本機影片。

To update the YouTube downloader in the environment:  
更新 YouTube 下載工具：

```bash
python -m pip install --upgrade "yt-dlp[default]"
```

## ElevenLabs allowance / ElevenLabs 額度

The dashboard calls:  
面板呼叫：

```text
GET /v1/user/subscription
```

It uses these returned fields:  
使用以下回傳欄位：

```text
character_count
character_limit
```

Displayed remaining included allowance is:  
顯示的剩餘內含額度為：

```text
max(0, character_limit - character_count)
```

This is not an LLM-token count, an exact dubbing cost estimate,
a cash balance, or a guaranteed total spending limit.  
這不是 LLM Token 數量、精確配音費用估算、現金餘額或保證可花費的總上限。

API-key limits, workspace restrictions, overage settings, and delayed
usage reporting can affect what is actually available.  
API 金鑰上限、工作區限制、額外用量設定及使用量更新延遲，
都可能影響實際可用額度。

The app refreshes allowance after a dubbing or resume operation.
You can also refresh it manually.  
配音或繼續查詢操作結束後會更新額度，也可手動更新。

If the key cannot read subscription information, the dashboard reports
an error instead of inventing a balance.  
如果金鑰無法讀取訂閱資訊，面板會顯示錯誤，不會捏造餘額。

## Paid requests and resume / 付費請求與繼續查詢

Creating a dubbing project can incur a charge before final audio exists.  
建立配音專案可能在最終音訊產生前就扣除額度。

The application never automatically retries the project-creation POST.  
本工具不會自動重送建立專案的 POST 請求。

If creation times out before returning an ID, check ElevenLabs before
trying again. The app marks that local job as uncertain.  
如果建立請求逾時且沒有回傳 ID，請先檢查 ElevenLabs。
本工具會將該本機工作標記為建立結果不明。

Resume polls an existing project from the current session. It does not
create a new project or regenerate failed/stale targets.  
繼續查詢只會查詢目前工作階段的既有專案，
不會建立新專案，也不會重新生成失敗或過期的目標。

Resume requires the local prepared clip and session state to remain
available. This version does not restore jobs after a browser/runtime reset.  
繼續查詢需要保留本機片段及工作階段狀態。
本版本不提供瀏覽器或執行階段重設後的工作還原。

Closing the browser is not a cancellation request to ElevenLabs.  
關閉瀏覽器不等於向 ElevenLabs 取消工作。

## Subtitles versus speech / 字幕與語音的差別

Editing a subtitle changes text only. It does not regenerate audio.  
修改字幕只會改變文字，不會重新生成音訊。

Original captions are recognized from original audio.  
原文字幕由原始音訊辨識產生。

Translated captions are recognized from dubbed audio. They are not a
guaranteed exact copy of the provider's internal translation script.  
翻譯字幕由配音音訊辨識產生，不保證與服務內部翻譯稿完全一致。

Traditional Chinese conversion changes recognized text, not the voice,
accent, or spoken language variant.  
繁體中文轉換只改變辨識文字，不會改變聲音、口音或語言變體。

Preparing a clip without dubbing generates original captions only.
You can import your own translated SRT.  
只準備影片而不配音時，只會產生原文字幕。
您可以自行匯入翻譯 SRT。

Automatic recognition and translation can be wrong. Review all output.  
自動辨識與翻譯可能有誤，請檢查所有輸出。

## Live editing / 即時編輯

Commit a table edit with Enter or by clicking outside the cell.  
按 Enter 或點選儲存格外完成編輯。

The browser updates captions without re-encoding the video.  
瀏覽器更新字幕時不會重新編碼影片。

This is interactive editing of prepared media, not live-stream
transcription.  
這是已準備媒體的互動編輯，不是直播逐字稿辨識。

Timing is relative to the prepared clip, starting at zero.  
時間以裁切後的片段為準，從零開始。

Valid timing:  
有效時間格式：

```text
0 <= start < end
```

Export clips or drops captions beyond the end of the prepared video.  
匯出時會裁切或略過超出片段結尾的字幕。

Imported full-video SRT files are not automatically shifted by the source
clip start. Use the timing-shift controls if necessary.  
匯入完整影片的 SRT 時，不會自動扣除裁切開始時間。
必要時請使用字幕時間位移功能。

## Export / 匯出

### Soft subtitles / 可關閉字幕

Copies the prepared video and audio streams and adds a selectable
MP4 subtitle track.  
複製已準備的影像及音訊串流，加入可選擇的 MP4 字幕軌。

Playback requires subtitle-track support. Some destinations may ignore
the track.  
播放需要字幕軌支援，部分平台可能忽略字幕軌。

### Burn in / 永久燒錄

Renders captions into the image and re-encodes the video.  
將字幕寫入畫面並重新編碼影片。

Browser preview typography can differ from burned-in typography.  
瀏覽器預覽的字型排版可能與燒錄結果不同。

### No subtitles / 無字幕

Copies the selected prepared video without adding captions.  
複製所選影片，不加入字幕。

### Downloaded files / 下載檔案

Depending on available captions:  
依可用字幕提供：

```text
video.mp4
original.srt
translated.srt
selected.srt
selected.vtt
subtitle_edits.json
```

The JSON file is an edit backup; this version does not provide JSON import.  
JSON 檔案用於備份編輯內容；本版本不提供 JSON 匯入功能。

## App limits / 工具限制

- Final uploaded/downloaded source: 500 MB.  
  最終上傳或下載的來源檔案：500 MB。
- YouTube videos: non-live, up to 20 minutes.  
  YouTube：非直播，最長 20 分鐘。
- Requested clip length: 5–120 seconds.  
  指定片段長度：5–120 秒。
- SRT input: 2 MB.  
  SRT 輸入：2 MB。
- Maximum 2,000 nonempty rows per subtitle table.  
  每個字幕表最多 2,000 個非空白列。
- One heavy processing task at a time.  
  同一時間只處理一項大型工作。
- Cloud polling stops after about 30 minutes; use Resume afterward.  
  雲端查詢約 30 分鐘後停止，可再使用繼續查詢。

Temporary downloads and exports can use more disk space than the final
source-file limit.  
暫存下載及匯出檔案可能使用超過最終來源檔案上限的磁碟空間。

The app keeps the prepared video's duration. Dubbed audio is trimmed
or padded to match it. Lip synchronization is not guaranteed.  
本工具保留影片片段長度，配音會依需要裁切或補靜音，
不保證嘴型同步。

Caption recognition uses CPU/int8 by default.  
字幕辨識預設使用 CPU/int8。

The first recognition run downloads a model.  
首次辨識需要下載模型。

The animated activity indicator is not a completion percentage.  
動態處理指示不是完成百分比。

## Privacy and cleanup / 隱私及清理

Only use media and voices you have permission to process.  
僅處理您有權使用的媒體及聲音。

Your API key is handled by the server hosting this app.
Enter it only into a trusted instance.  
API 金鑰由執行本工具的伺服器處理，
請只在可信任的執行環境輸入。

Application code does not intentionally write the key to job JSON.
Gradio run history is disabled.  
程式不會刻意將金鑰寫入工作 JSON，並停用 Gradio 執行歷程。

Prepared audio is sent to ElevenLabs when you create a dub.  
建立配音時，準備好的音訊會傳送至 ElevenLabs。

This is not a hardened multi-user hosting service.
Optional login does not provide per-user file isolation.  
這不是強化隔離的多人主機服務，
選用登入也不提供個別使用者檔案隔離。

Work files remain under:  
工作檔案位於：

```text
work/
```

After downloading what you need, stop the app and delete that directory
to remove local working media.  
下載需要的檔案後，請先停止程式，再刪除該目錄以移除本機工作媒體。

Deleting local files does not delete cloud projects at ElevenLabs.  
刪除本機檔案不會刪除 ElevenLabs 雲端專案。

## Tests / 測試

```bash
python -m unittest discover -s tests -v
python -m py_compile app.py engine.py youtube_urls.py
```

Included tests cover URL normalization and rejection rules only.  
內附測試僅涵蓋網址標準化及拒絕規則。

They do not test YouTube network access, FFmpeg output, browser behavior,
or paid ElevenLabs requests.  
測試不包含 YouTube 網路存取、FFmpeg 輸出、瀏覽器行為
或付費 ElevenLabs 請求。

Before paid use, test a short clip and review all output.  
付費使用前，請先以短片測試並檢查輸出。

## API and dependency references / API 與相依套件參考

```text
https://elevenlabs.io/docs/api-reference/dubbing/create-project
https://elevenlabs.io/docs/api-reference/dubbing/get-project
https://elevenlabs.io/docs/api-reference/dubbing/language-targets/get-language-target
https://elevenlabs.io/docs/api-reference/user/subscription/get
https://github.com/yt-dlp/yt-dlp
https://github.com/yt-dlp/yt-dlp/wiki/EJS
https://www.gradio.app/docs
```
