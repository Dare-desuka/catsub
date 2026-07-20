#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install_one() {
    local name="$1"
    local dir="$ROOT_DIR/$name"
    echo "==> Installing $name"
    cd "$dir"
    [ ! -d venv ] && python3 -m venv venv
    venv/bin/python -m pip install --upgrade pip
    venv/bin/pip install -r requirements.txt
}

install_one whisper
install_one srt
echo "Done."
