#!/usr/bin/env bash
set -Eeuo pipefail

# Prevent inherited notebook Python settings from affecting this environment.
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

trap 'echo >&2 "❌ Launcher failed at line $LINENO. Read the error immediately above."' ERR

echo "==============================================="
echo "影片配音 v2 / ElevenLabs Video Dubber v2"
echo "==============================================="

if [[ "$(id -u)" -eq 0 ]]; then
    SUDO=()
else
    SUDO=(sudo)
fi

echo
echo "① Checking project files / 檢查專案檔案"

REQUIRED_FILES=(
    app.py
    engine.py
    youtube_urls.py
    requirements.txt
    assets/style.css
    assets/captions.js
    tests/test_urls.py
)

for file in "${REQUIRED_FILES[@]}"; do
    if [[ ! -f "$ROOT/$file" ]]; then
        echo "❌ Missing file / 缺少檔案: $file" >&2
        echo "Upload all project files before running this launcher." >&2
        exit 1
    fi
done

# Catch the previously reported problem before installing anything.
if head -n 5 app.py | grep -Eq \
    '^#!.*bash|^[[:space:]]*set[[:space:]]+-Eeuo'; then
    echo "❌ app.py contains Bash code, not the Python application." >&2
    echo "請將第 7 節的 Python 程式存入 app.py。" >&2
    echo "This launcher belongs in colab.sh, NOT app.py." >&2
    exit 1
fi

echo
echo "② Installing system tools / 安裝系統工具"

"${SUDO[@]}" apt-get update -qq

"${SUDO[@]}" apt-get install -y -qq \
    python3.12 \
    python3.12-venv \
    ffmpeg \
    fonts-noto-cjk \
    fonts-noto-core \
    fontconfig \
    curl \
    unzip \
    ca-certificates

# Use the interpreter matching the explicitly installed venv package,
# rather than whichever python3 happens to appear first on PATH.
BASE_PYTHON="/usr/bin/python3.12"

if [[ ! -x "$BASE_PYTHON" ]]; then
    echo "❌ Python not found: $BASE_PYTHON" >&2
    exit 1
fi

"$BASE_PYTHON" --version

echo
echo "③ Checking Python source / 檢查 Python 原始碼"

# Compile source without importing the app or requiring its dependencies.
"$BASE_PYTHON" - <<'PY'
from pathlib import Path

for name in (
    "app.py",
    "engine.py",
    "youtube_urls.py",
    "tests/test_urls.py",
):
    path = Path(name)
    compile(path.read_bytes(), str(path), "exec")
    print(f"OK: {name}")
PY

echo
echo "④ Preparing virtual environment / 準備虛擬環境"

VENV="$ROOT/.venv"
PYTHON="$VENV/bin/python"

# Reuse only a working Python 3.12 virtual environment with pip.
if [[ -x "$PYTHON" ]] && \
    "$PYTHON" -c '
import sys
import pip
assert sys.version_info[:2] == (3, 12)
assert sys.prefix != sys.base_prefix
' >/dev/null 2>&1; then
    echo "Using existing environment / 使用現有環境"
else
    # Preserve the old environment instead of deleting it.
    if [[ -e "$VENV" || -L "$VENV" ]]; then
        BACKUP_DIR="$(mktemp -d "$ROOT/.venv-backup.XXXXXX")"
        mv -- "$VENV" "$BACKUP_DIR/venv"
        echo "Old environment saved / 舊環境備份: $BACKUP_DIR/venv"
    fi

    # Separate environment creation from pip bootstrapping.
    "$BASE_PYTHON" -m venv --without-pip "$VENV"

    ENSUREPIP_LOG="$VENV/ensurepip-install.log"

    if "$PYTHON" -m ensurepip --upgrade --default-pip \
        >"$ENSUREPIP_LOG" 2>&1; then
        echo "pip installed using ensurepip."
    else
        echo "ensurepip failed. Diagnostic output:"
        cat "$ENSUREPIP_LOG"

        echo
        echo "Installing pip using the official PyPA bootstrap script."

        BOOTSTRAP="$VENV/get-pip.py"

        curl --fail --show-error --location \
            --retry 3 \
            --connect-timeout 20 \
            https://bootstrap.pypa.io/get-pip.py \
            --output "$BOOTSTRAP"

        "$PYTHON" "$BOOTSTRAP"
        rm -f -- "$BOOTSTRAP"
    fi
fi

# Prefer the application environment for tools launched by subprocesses.
export PATH="$VENV/bin:$PATH"

"$PYTHON" -m pip --version

echo
echo "⑤ Installing application dependencies / 安裝應用程式套件"

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r "$ROOT/requirements.txt"
"$PYTHON" -m pip check

echo
echo "⑥ Preparing Deno / 準備 Deno"

export DENO_INSTALL="${DENO_INSTALL:-$HOME/.deno}"
export PATH="$DENO_INSTALL/bin:$PATH"

if ! command -v deno >/dev/null 2>&1; then
    INSTALLER="$(mktemp)"

    curl --fail --show-error --location \
        --retry 3 \
        --connect-timeout 20 \
        https://deno.land/install.sh \
        --output "$INSTALLER"

    sh "$INSTALLER" -y
    rm -f -- "$INSTALLER"
fi

if ! command -v deno >/dev/null 2>&1; then
    echo "❌ Deno installation failed / Deno 安裝失敗" >&2
    exit 1
fi

echo
echo "⑦ Running checks / 執行檢查"

"$PYTHON" -m unittest discover -s tests -v
"$PYTHON" -m py_compile app.py engine.py youtube_urls.py

deno --version
"$PYTHON" -m yt_dlp --version

echo
echo "⑧ Starting application / 啟動應用程式"
echo "Keep this cell running / 請保持此儲存格執行"
echo "Without APP_USER and APP_PASSWORD, the share link has no login."
echo

exec "$PYTHON" "$ROOT/app.py" --share "$@"
