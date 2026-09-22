#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "==============================================="
echo "影片配音 v2 / ElevenLabs Video Dubber v2"
echo "==============================================="

if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi

"${SUDO[@]}" apt-get update -qq
"${SUDO[@]}" apt-get install -y -qq \
    ffmpeg \
    fonts-noto-cjk \
    fonts-noto-core \
    fontconfig \
    python3-venv \
    curl \
    unzip \
    ca-certificates

export DENO_INSTALL="${DENO_INSTALL:-$HOME/.deno}"
export PATH="$DENO_INSTALL/bin:$PATH"

if ! command -v deno >/dev/null 2>&1; then
    echo "安裝 Deno / Installing Deno"

    INSTALLER="$(mktemp)"
    curl -fsSL https://deno.land/install.sh -o "$INSTALLER"
    sh "$INSTALLER" -y
    rm -f "$INSTALLER"
fi

if ! command -v deno >/dev/null 2>&1; then
    echo "Deno 安裝失敗 / Deno installation failed"
    exit 1
fi

python3 -m venv .venv

PYTHON="$ROOT/.venv/bin/python"

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install --upgrade -r requirements.txt

echo
echo "執行網址測試 / Running URL tests"
"$PYTHON" -m unittest discover -s tests -v

echo
echo "檢查 Python 語法 / Checking Python syntax"
"$PYTHON" -m py_compile app.py engine.py youtube_urls.py

echo
echo "工具版本 / Tool versions"
deno --version
"$PYTHON" -m yt_dlp --version

echo
echo "啟動公開分享連結 / Starting public share link"
echo "請保持此儲存格執行 / Keep this cell running"
echo

exec "$PYTHON" app.py --share "$@"
