#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "CatSub dependency installer"
echo
echo "Choose PyTorch variant:"
echo "  1) CPU-only  - smaller download, safe for most PCs"
echo "  2) GPU/CUDA  - NVIDIA PC, larger download"
echo
read -r -p "Choice [1/2] (default: 1): " choice

case "${choice:-1}" in
    1|cpu|CPU)
        REQ_FILE="requirements.txt"
        PIP_EXTRA_ARGS=(--extra-index-url https://download.pytorch.org/whl/cpu)
        VARIANT="CPU-only"
        ;;
    2|gpu|GPU|cuda|CUDA)
        REQ_FILE="requirements.gpu.txt"
        PIP_EXTRA_ARGS=()
        VARIANT="GPU/CUDA"
        ;;
    *)
        echo "Invalid choice: $choice"
        exit 1
        ;;
esac

install_one() {
    local name="$1"
    local dir="$ROOT_DIR/$name"

    echo
    echo "==> Installing $name ($VARIANT)"
    cd "$dir"

    if [ ! -d venv ]; then
        python3 -m venv venv
    fi

    venv/bin/python -m pip install --upgrade pip
    venv/bin/pip install -r "$REQ_FILE" "${PIP_EXTRA_ARGS[@]}"
}

install_one whisper
install_one srt

echo
echo "Done. Selected variant: $VARIANT"
