#!/bin/bash
set -uo pipefail

# ─── Konfigurasi Path ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHISPER_DIR="$SCRIPT_DIR/whisper"
SRT_DIR="$SCRIPT_DIR/srt"

VIDEO_DIR="$WHISPER_DIR/videos"
WHISPER_SRT_DIR="$WHISPER_DIR/transcripts"
SRT_INPUT_DIR="$SRT_DIR/queue"
SRT_OUTPUT_DIR="$SRT_DIR/subtitles"

# FORCE=1 ./sub.sh  → paksa ulang semua proses
FORCE="${FORCE:-0}"

# AUTO=1 ./sub.sh   → jalan terus tanpa pause (non-interaktif / cron)
AUTO="${AUTO:-0}"

# MKV=1 ./sub.sh    → mux video asli + subtitle ID jadi .mkv (softsub, buang sub lama)
MKV="${MKV:-0}"
MKV_OUT_DIR="$SCRIPT_DIR/output"

# CLEAN=1 ./sub.sh  → setelah MKV sukses, kirim video asli + SRT (whisper cache & _ID)
#                     ke trash (gio trash), bukan hapus permanen. Butuh MKV=1.
CLEAN="${CLEAN:-0}"

# SUB_LANG=auto ./sub.sh → deteksi otomatis bahasa video
# SUB_LANG=ja|zh|en      → paksa bahasa Jepang / China / Inggris
# LANG=ja ./sub.sh juga didukung, tapi LANG locale sistem tidak akan dipakai.
SUBTITLE_LANG="${SUB_LANG:-${VIDEO_LANG:-}}"
if [ -z "$SUBTITLE_LANG" ]; then
    case "${LANG:-}" in
        auto|ja|zh|en) SUBTITLE_LANG="$LANG" ;;
        *) SUBTITLE_LANG="auto" ;;
    esac
fi
SUBTITLE_LANG="${SUBTITLE_LANG,,}"

# ─── Warna ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

print_header() {
    echo ""
    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}${CYAN}║  🎬  AI SUBTITLE PIPELINE · SAFE SEQUENTIAL      ║${RESET}"
    echo -e "${BOLD}${CYAN}║  1 video = transcribe → translate → pause/lanjut ║${RESET}"
    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${RESET}"
    echo ""
}

info() { echo -e "${DIM}${BLUE}│${RESET}  ${YELLOW}⟶${RESET}  $1"; }
ok()   { echo -e "${GREEN}└─ ✅ $1${RESET}"; }
warn() { echo -e "${YELLOW}└─ ⚠️  $1${RESET}"; }
fail() { echo -e "\n${RED}╳ ERROR: $1${RESET}\n"; exit 1; }

# ─── Progress ─────────────────────────────────────────────────────────────────
# Spinner untuk pipeline step. Python scripts tangani progress detail sendiri.
# System tool `progress` (pacman) bisa dipakai terpisah di terminal lain.

run_with_spinner() {
    local step="$1"
    shift
    local pid
    local spin='-\|/'
    local i=0
    (
        while true; do
            printf "\r  ${DIM}${BLUE}│${RESET}  ${step}... ${CYAN}%c${RESET}" "${spin:i%4:1}"
            ((i++))
            sleep 0.2
        done
    ) &
    pid=$!
    "$@"
    local code=$?
    kill $pid 2>/dev/null; wait $pid 2>/dev/null
    printf "\r  ${DIM}${BLUE}│${RESET}  ${step}... ${GREEN}✓${RESET}\n"
    return $code
}

# ─── Venv runner ──────────────────────────────────────────────────────────────
# FIX: subshell ( ) agar 'cd' tidak mengubah cwd proses utama
run_in_venv() {
    local workdir="$1"
    shift
    (
        cd "$workdir" || exit 1
        source venv/bin/activate || exit 1
        "$@"
        local code=$?
        deactivate 2>/dev/null || true
        exit $code
    )
}

# ─── Helpers ──────────────────────────────────────────────────────────────────
clean_srt_input() {
    mkdir -p "$SRT_INPUT_DIR"
    rm -f "$SRT_INPUT_DIR"/*.srt 2>/dev/null || true
}

# ponytail: kirim ke trash biar bisa dipulihkan. gio trash → ~/.local/share/Trash.
to_trash() {
    gio trash "$1" 2>/dev/null || rm -f "$1"
}

validate_lang() {
    case "$1" in
        auto|ja|zh|en) return 0 ;;
        *) return 1 ;;
    esac
}

lang_label() {
    case "$1" in
        auto) echo "auto-detect" ;;
        ja) echo "Jepang" ;;
        zh) echo "China" ;;
        en) echo "Inggris" ;;
        *) echo "$1" ;;
    esac
}

read_detected_lang() {
    local lang_file="$1"
    if [ -s "$lang_file" ]; then
        tr -d '[:space:]' < "$lang_file"
    fi
}

# ─── Pause interaktif setelah 1 video selesai ────────────────────────────────
# Return 0 = lanjut, return 1 = stop
pause_and_ask() {
    local stem="$1"
    local final_srt="$2"
    local remaining="$3"   # jumlah video tersisa

    echo ""
    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}${CYAN}║  ⏸   PAUSE — Video selesai diproses              ║${RESET}"
    echo -e "${BOLD}${CYAN}╠══════════════════════════════════════════════════╣${RESET}"
    printf  "${BOLD}${CYAN}║${RESET}  %-47s${BOLD}${CYAN}║${RESET}\n" "File  : ${stem}_ID.srt"
    printf  "${BOLD}${CYAN}║${RESET}  %-47s${BOLD}${CYAN}║${RESET}\n" "Path  : $SRT_OUTPUT_DIR/"
    printf  "${BOLD}${CYAN}║${RESET}  %-47s${BOLD}${CYAN}║${RESET}\n" "Sisa  : $remaining video lagi"
    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${RESET}"
    echo ""

    if [ "$remaining" -eq 0 ]; then
        # Ini video terakhir, tidak perlu tanya
        return 0
    fi

    while true; do
        echo -ne "  ${BOLD}Lanjut ke video berikutnya?${RESET}  "
        echo -ne "[${GREEN}L${RESET}]anjut  [${RED}S${RESET}]top  : "
        read -r -n 1 jawaban </dev/tty
        echo ""

        case "${jawaban,,}" in
            l|"")
                echo -e "  ${GREEN}▶  Lanjut...${RESET}"
                echo ""
                return 0
                ;;
            s)
                echo ""
                echo -e "  ${YELLOW}⏹  Dihentikan oleh pengguna.${RESET}"
                echo -e "  ${DIM}Jalankan lagi untuk melanjutkan — video yang sudah selesai akan di-skip otomatis.${RESET}"
                echo ""
                return 1
                ;;
            *)
                echo -e "  ${RED}Tekan L untuk lanjut atau S untuk stop.${RESET}"
                ;;
        esac
    done
}

# ─────────────────────────────────────────────────────────────────────────────

print_header
START_TIME=$SECONDS

validate_lang "$SUBTITLE_LANG" || fail "Bahasa tidak valid: '$SUBTITLE_LANG'. Pakai SUB_LANG=auto|ja|zh|en"

mkdir -p "$VIDEO_DIR" "$WHISPER_SRT_DIR" "$SRT_INPUT_DIR" "$SRT_OUTPUT_DIR"
[ "$MKV" = "1" ] && mkdir -p "$MKV_OUT_DIR"

mapfile -d '' VIDEO_LIST < <(find "$VIDEO_DIR" -maxdepth 1 \
    \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" \
       -o -iname "*.mov" -o -iname "*.webm" -o -iname "*.flv" \) \
    -print0 2>/dev/null | sort -z)

if [ "${#VIDEO_LIST[@]}" -eq 0 ]; then
    fail "Tidak ada video di $VIDEO_DIR"
fi

echo -e "${BOLD}${BLUE}┌─ Ditemukan ${#VIDEO_LIST[@]} video${RESET}"
info "Mode aman: 1 video selesai → pause untuk review."
info "FORCE=$FORCE  |  AUTO=$AUTO  |  SUB_LANG=$(lang_label "$SUBTITLE_LANG")  |  MKV=$MKV  |  CLEAN=$CLEAN"

if [ "$AUTO" = "1" ]; then
    info "${YELLOW}Mode AUTO aktif — tidak akan pause.${RESET}"
fi
echo -e "${DIM}${BLUE}│${RESET}"

SUCCESS=0
SKIPPED=0
FAILED=0

# Hitung dulu berapa yang perlu diproses (untuk hitung "sisa")
NEED_PROCESS=()
for video in "${VIDEO_LIST[@]}"; do
    base="$(basename "$video")"
    stem="${base%.*}"
    final_srt="$SRT_OUTPUT_DIR/${stem}_ID.srt"
    if [ ! -f "$final_srt" ] || [ "$FORCE" = "1" ]; then
        NEED_PROCESS+=("$video")
    fi
done

TOTAL_NEED=${#NEED_PROCESS[@]}
PROCESSED=0   # counter video yang sudah selesai dalam sesi ini

for idx in "${!VIDEO_LIST[@]}"; do
    video="${VIDEO_LIST[$idx]}"
    base="$(basename "$video")"
    stem="${base%.*}"

    whisper_srt="$WHISPER_SRT_DIR/$stem.srt"
    whisper_lang_file="$WHISPER_SRT_DIR/$stem.lang"
    srt_input_srt="$SRT_INPUT_DIR/$stem.srt"
    final_srt="$SRT_OUTPUT_DIR/${stem}_ID.srt"

    echo ""
    echo -e "${BOLD}${BLUE}┌─ Video $((idx + 1))/${#VIDEO_LIST[@]}: ${CYAN}$base${RESET}"
    echo -e "${DIM}${BLUE}│${RESET}"

    # ── Skip jika sudah ada output final & tidak ada yg gagal ────────────────
    # ponytail: auto-resume — kalau masih ada marker [BELUM_DITERJEMAHKAN],
    # tetap proses (srt.py lanjutkan cuma baris gagal).
    if [ -f "$final_srt" ] && [ "$FORCE" != "1" ]; then
        if grep -q "\[BELUM_DITERJEMAHKAN\]" "$final_srt"; then
            info "Output ada tapi ada baris gagal — lanjutkan translate (resume)."
        else
            info "Output final sudah ada: $final_srt"
            ok "Skip video ini."
            ((SKIPPED++))
            continue
        fi
    fi

    # FIX: clean SEBELUM cek whisper_srt, bukan hanya setelah transcribe
    clean_srt_input

    # ── FORCE: hapus output lama video ini saja ───────────────────────────────
    if [ "$FORCE" = "1" ]; then
        rm -f "$whisper_srt" "$whisper_lang_file" "$final_srt"
        info "FORCE aktif: output lama video ini dihapus."
    fi

    if [ -f "$whisper_srt" ] && [ "$SUBTITLE_LANG" = "auto" ] && [ ! -s "$whisper_lang_file" ]; then
        warn "SRT Whisper lama tidak punya metadata bahasa, transcribe ulang untuk auto-detect."
        rm -f "$whisper_srt"
    fi

    # ── 1) TRANSCRIBE ─────────────────────────────────────────────────────────
    if [ -f "$whisper_srt" ]; then
        info "SRT Whisper sudah ada, pakai ulang: $whisper_srt"
    else
        info "Transcribe 1 video saja..."
        run_with_spinner "Transcribe" \
            run_in_venv "$WHISPER_DIR" python3 transcribe.py \
                --lang "$SUBTITLE_LANG" --video "$video" --output "$whisper_srt"
        code=$?
        if [ $code -ne 0 ] || [ ! -s "$whisper_srt" ]; then
            warn "Transcribe gagal untuk $base"
            ((FAILED++))
            clean_srt_input
            continue
        fi
    fi

    SRC_LANG="$(read_detected_lang "$whisper_lang_file")"
    if [ -z "$SRC_LANG" ] && [ "$SUBTITLE_LANG" != "auto" ]; then
        SRC_LANG="$SUBTITLE_LANG"
    fi
    if ! validate_lang "$SRC_LANG" || [ "$SRC_LANG" = "auto" ]; then
        warn "Bahasa sumber tidak valid/terdeteksi untuk $base"
        ((FAILED++))
        clean_srt_input
        continue
    fi
    info "Bahasa sumber: $(lang_label "$SRC_LANG")"

    # ── 2) COPY ke SRT input (folder sudah bersih) ───────────────────────────
    cp -f "$whisper_srt" "$srt_input_srt" || {
        warn "Gagal copy SRT ke SRT input."
        ((FAILED++))
        continue
    }
    info "Input translate dikunci 1 file: $srt_input_srt"

    # ── 3) TRANSLATE ──────────────────────────────────────────────────────────
    info "Translate 1 SRT saja..."
    run_with_spinner "Translate" \
        run_in_venv "$SRT_DIR" python3 srt.py \
            --lang "$SRC_LANG" --input "$srt_input_srt" --output "$final_srt"
    code=$?
    if [ $code -ne 0 ] || [ ! -s "$final_srt" ]; then
        warn "Translate gagal untuk $stem.srt"
        ((FAILED++))
        clean_srt_input
        continue
    fi

    # ── 4) CLEANUP temp ───────────────────────────────────────────────────────
    # ponytail: cuma hapus temp per-video (srt_input). whisper_srt + .lang
    # disimpan sbg cache supaya translate yg gagal bisa diulang tanpa transcribe 2x.
    rm -f "$srt_input_srt"
    info "Temp SRT video ini dihapus."

    # ── 4.5) MUX ke MKV (softsub) ─────────────────────────────────────────────
    # ponytail: -c copy (no re-encode), buang sub lama (-map -0:s), embed ID sbg
    # softsub track bahasa indonesia. Output selalu .mkv.
    if [ "$MKV" = "1" ]; then
        mkv_out="$MKV_OUT_DIR/$stem.mkv"
        if [ -f "$mkv_out" ] && [ "$FORCE" != "1" ]; then
            info "MKV sudah ada: $mkv_out (skip)"
        else
            # ponytail: video+audio dari input asli, sub ID dari SRT. Buang sub
            # lama (-map 0:v -map 0:a), embed ID sbg softsub bahasa indonesia.
            # -map 0:v -map 0:a -map 1 → sub selalu stream #2 (metadata deterministik).
            run_with_spinner "Mux MKV" \
                ffmpeg -y -i "$video" -i "$final_srt" \
                -map 0:v -map 0:a -map 1 \
                -c:v copy -c:a copy -c:s srt \
                -metadata:s:2 language=ind -metadata:s:2 title="Indonesia" \
                -disposition:s:2 default \
                "$mkv_out"
            if [ $? -eq 0 ] && [ -s "$mkv_out" ]; then
                ok "MKV: $mkv_out"
                # ponytail: CLEAN=1 → trash source setelah softsub sukses, bukan
                # hapus permanen (bisa dipulihkan dari ~/.local/share/Trash).
                if [ "$CLEAN" = "1" ]; then
                    to_trash "$video"
                    to_trash "$whisper_srt"
                    to_trash "$whisper_lang_file"
                    to_trash "$final_srt"
                    info "CLEAN: video + SRT dikirim ke trash."
                fi
            else
                warn "Mux gagal untuk $stem"
            fi
        fi
    fi

    ok "Selesai: $final_srt"
    ((SUCCESS++))
    ((PROCESSED++))

    # ── 5) PAUSE — tanya lanjut/stop (kecuali AUTO=1) ─────────────────────────
    REMAINING=$((TOTAL_NEED - PROCESSED))

    if [ "$AUTO" != "1" ]; then
        pause_and_ask "$stem" "$final_srt" "$REMAINING" || break
    fi
done

# Pastikan tidak ada sisa SRT di input translate
clean_srt_input

ELAPSED=$((SECONDS - START_TIME))
MINS=$((ELAPSED / 60))
SECS=$((ELAPSED % 60))

echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║  SELESAI                                         ║${RESET}"
echo -e "${BOLD}${GREEN}╠══════════════════════════════════════════════════╣${RESET}"
printf  "${BOLD}${GREEN}║${RESET}  %-47s${BOLD}${GREEN}║${RESET}\n" "Berhasil : $SUCCESS video"
printf  "${BOLD}${GREEN}║${RESET}  %-47s${BOLD}${GREEN}║${RESET}\n" "Skip     : $SKIPPED video"
printf  "${BOLD}${GREEN}║${RESET}  %-47s${BOLD}${GREEN}║${RESET}\n" "Gagal    : $FAILED video"
printf  "${BOLD}${GREEN}║${RESET}  %-47s${BOLD}${GREEN}║${RESET}\n" "Durasi   : ${MINS}m ${SECS}s"
printf  "${BOLD}${GREEN}║${RESET}  %-47s${BOLD}${GREEN}║${RESET}\n" "Output   : $SRT_OUTPUT_DIR/"
if [ "$MKV" = "1" ]; then
    printf  "${BOLD}${GREEN}║${RESET}  %-47s${BOLD}${GREEN}║${RESET}\n" "MKV     : $MKV_OUT_DIR/"
fi
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════╝${RESET}"
echo ""

[ "$FAILED" -gt 0 ] && exit 1
exit 0
