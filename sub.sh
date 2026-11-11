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
# SUB_LANG=ja|zh|en|ko|th|tl|ru  → paksa bahasa sumber
# LANG=ja ./sub.sh juga didukung, tapi LANG locale sistem tidak akan dipakai.
SUBTITLE_LANG="${SUB_LANG:-${VIDEO_LANG:-}}"
if [ -z "$SUBTITLE_LANG" ]; then
    case "${LANG:-}" in
        auto|ja|zh|en|ko|th|tl|ru) SUBTITLE_LANG="$LANG" ;;
        *) SUBTITLE_LANG="auto" ;;
    esac
fi
SUBTITLE_LANG="${SUBTITLE_LANG,,}"

# ─── Debug / dry-run / JSON log ────────────────────────────────────────────────
VERBOSE="${VERBOSE:-0}"
DRY_RUN="${DRY_RUN:-0}"
JSON_LOG="${JSON_LOG:-}"
# ponytail: flag CLI minimal — --verbose dump raw Groq/ffprobe, --dry-run preview tanpa Groq, --json-log tulis pipeline.json
# argumen posisi = path video eksplisit (./sub.sh ~/a.mp4 /mnt/b.mkv), tanpa harus taruh di whisper/videos
CLI_VIDEOS=()
for arg in "$@"; do
    case "$arg" in
        --verbose|-v) VERBOSE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --json-log) JSON_LOG="$SCRIPT_DIR/pipeline.json" ;;
        --json-log=*) JSON_LOG="${arg#--json-log=}" ;;
        -*) ;;
        *) CLI_VIDEOS+=("$arg") ;;
    esac
done

# ponytail: JSON log terstruktur — satu line per event, append
json_log() {
    [ -z "$JSON_LOG" ] && return 0
    mkdir -p "$(dirname "$JSON_LOG")" 2>/dev/null || true
    printf '%s\n' "$1" >> "$JSON_LOG"
}
# ponytail: extra args untuk python scripts biar verbose/dry-run/json konsisten
WHISPER_EXTRA=""
SRT_EXTRA=""
[ "$VERBOSE" = "1" ] && WHISPER_EXTRA="$WHISPER_EXTRA --verbose" && SRT_EXTRA="$SRT_EXTRA --verbose"
[ -n "$JSON_LOG" ] && WHISPER_EXTRA="$WHISPER_EXTRA --json-log $JSON_LOG" && SRT_EXTRA="$SRT_EXTRA --json-log $JSON_LOG"
[ "$DRY_RUN" = "1" ] && WHISPER_EXTRA="$WHISPER_EXTRA --dry-run" && SRT_EXTRA="$SRT_EXTRA --dry-run"

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
    # ponytail: TUI_QUIET — TUI punya judul panel sendiri, box dekoratif dibuang
    [ -n "${TUI_QUIET:-}" ] && return 0
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
    # ponytail: TUI_QUIET=1 (dari tui.py) — TUI gambar animasinya sendiri
    # (spinner braille + bar), frame spinner bash cuma jadi sampah log.
    if [ -n "${TUI_QUIET:-}" ]; then
        info "$step..."
        "$@"
        return $?
    fi
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
        # ponytail: tanpa ini stdout python di-buffer saat di-pipe (TUI) —
        # panel log kosong ber-menit2 padahal job jalan (kelihatan hang)
        export PYTHONUNBUFFERED=1
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
        auto|ja|zh|en|ko|th|tl|ru) return 0 ;;
        *) return 1 ;;
    esac
}

lang_label() {
    case "$1" in
        auto) echo "auto-detect" ;;
        ja) echo "Jepang" ;;
        zh) echo "China" ;;
        en) echo "Inggris" ;;
        ko) echo "Korea" ;;
        th) echo "Thailand" ;;
        tl) echo "Filipina" ;;
        ru) echo "Rusia" ;;
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

# ─── Menu interaktif ────────────────────────────────────────────────────────────
show_menu() {
    echo ""
    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}${CYAN}║            PILIH MODE KERJA                      ║${RESET}"
    echo -e "${BOLD}${CYAN}╠══════════════════════════════════════════════════╣${RESET}"
    echo -e "${BOLD}${CYAN}║${RESET}  1) Subtitle SRT saja                       ${BOLD}${CYAN}║${RESET}"
    echo -e "${BOLD}${CYAN}║${RESET}  2) SRT + Mux MKV (softsub)                 ${BOLD}${CYAN}║${RESET}"
    echo -e "${BOLD}${CYAN}║${RESET}  3) SRT + MKV + Clean source                ${BOLD}${CYAN}║${RESET}"
    echo -e "${BOLD}${CYAN}║${RESET}  4) Auto SRT saja (tanpa pause)             ${BOLD}${CYAN}║${RESET}"
    echo -e "${BOLD}${CYAN}║${RESET}  5) Mux MKV saja (pilih SRT)                ${BOLD}${CYAN}║${RESET}"
    echo -e "${BOLD}${CYAN}║${RESET}  6) Scan konteks (QA proofread)             ${BOLD}${CYAN}║${RESET}"
    echo -e "${BOLD}${CYAN}║${RESET}  7) Path lain (ketik manual)                ${BOLD}${CYAN}║${RESET}"
    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════╝${RESET}"
    echo ""

    while true; do
        echo -ne "  Pilih [1-7]: "
        read -r pilihan </dev/tty
        case "$pilihan" in
            1) MKV=0; CLEAN=0; AUTO=0; FROM_MENU=1; info "Mode: SRT saja"; break ;;
            2) MKV=1; CLEAN=0; AUTO=0; FROM_MENU=1; info "Mode: SRT + MKV"; break ;;
            3) MKV=1; CLEAN=1; AUTO=0; FROM_MENU=1; info "Mode: SRT + MKV + Clean"; break ;;
            4) MKV=0; CLEAN=0; AUTO=1; FROM_MENU=1; info "Mode: Auto SRT (tanpa pause)"; break ;;
            5) MODE_MUX_SELECT=1; break ;;
            6) MODE_SCAN_CONTEXT=1; break ;;
            7) MODE_CUSTOM_PATH=1; MKV=0; CLEAN=0; AUTO=1; FROM_MENU=1; info "Mode: Path lain (auto)"; break ;;
            *) echo -e "  ${RED}Pilih 1-7${RESET}" ;;
        esac
    done
}

# ─── Path video manual (opsi 7: satu path per baris, kosong = selesai) ─────────
# ponytail: satu-per-baris biar path berspasi aman tanpa parsing kutip; glob
# ~/Videos/*.mp4 + folder (scan isi) di-expand per baris, validasi reuse aturan CLI.
ask_custom_paths() {
    echo ""
    echo -e "  ${BOLD}Ketik path video (satu per baris, Enter kosong = selesai):${RESET}"
    echo -e "  ${DIM}Contoh: ~/Videos/a.mp4  /mnt/data/*.mkv  /mnt/data/folder/${RESET}"
    CUSTOM_VIDEOS=()
    while true; do
        echo -ne "  path: "
        read -r p </dev/tty || break
        [ -z "$p" ] && break
        p="${p/#\~/$HOME}"
        if [ -d "$p" ]; then
            while IFS= read -r -d '' f; do
                CUSTOM_VIDEOS+=("$f")
            done < <(find "$p" -maxdepth 1 -type f \
                \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" \
                   -o -iname "*.mov" -o -iname "*.webm" -o -iname "*.flv" \) \
                -print0 2>/dev/null | sort -z)
        elif [[ "$p" == *[\*\?\[]* ]]; then
            expanded=($(compgen -G "$p"))
            [ "${#expanded[@]}" -eq 0 ] && { warn "Tidak cocok, skip: $p"; continue; }
            for f in "${expanded[@]}"; do
                [ -f "$f" ] || continue
                case "${f,,}" in
                    *.mp4|*.mkv|*.avi|*.mov|*.webm|*.flv) CUSTOM_VIDEOS+=("$f") ;;
                    *) warn "Ekstensi tak didukung, skip: $f" ;;
                esac
            done
        else
            if [ ! -f "$p" ]; then
                warn "File tidak ada, skip: $p"
                continue
            fi
            case "${p,,}" in
                *.mp4|*.mkv|*.avi|*.mov|*.webm|*.flv) CUSTOM_VIDEOS+=("$p") ;;
                *) warn "Ekstensi tak didukung, skip: $p" ;;
            esac
        fi
    done
    if [ "${#CUSTOM_VIDEOS[@]}" -eq 0 ]; then
        warn "Tidak ada path valid — kembali ke menu."
        return 1
    fi
    info "Terkumpul: ${#CUSTOM_VIDEOS[@]} video"
    return 0
}

# ─── Pilih SRT *_ID.srt (dipakai mux & scan konteks) ───────────────────────────
# ponytail: parser pilihan 1,3,7 / 1-5 / kosong=semua di-dup kecil dari filter
# video — array beda, ekstrak helper umum kalau nanti dipakai >2x.
select_srt_files() {
    # ponytail: TUI non-interaktif — daftar path via env newline-separated, skip prompt /dev/tty
    if [ -n "${TUI_SRTS:-}" ]; then
        SELECTED_SRTS=()
        while IFS= read -r f; do
            [ -f "$f" ] && SELECTED_SRTS+=("$f")
        done <<< "$TUI_SRTS"
        if [ "${#SELECTED_SRTS[@]}" -eq 0 ]; then
            warn "TUI_SRTS kosong/tidak valid."
            return 1
        fi
        return 0
    fi
    local -a srt_list=()
    while IFS= read -r -d '' f; do
        srt_list+=("$f")
    done < <(find "$SRT_OUTPUT_DIR" -maxdepth 1 -name "*_ID.srt" -print0 2>/dev/null | sort -z)

    if [ "${#srt_list[@]}" -eq 0 ]; then
        warn "Tidak ada *_ID.srt di $SRT_OUTPUT_DIR"
        return 1
    fi

    echo ""
    echo -e "  ${BOLD}Pilih SRT:${RESET}"
    local i
    for i in "${!srt_list[@]}"; do
        local base stem status has_video ext
        base="$(basename "${srt_list[$i]}")"
        stem="${base%_ID.srt}"
        status=""
        has_video=""
        for ext in mp4 mkv avi mov webm flv; do
            [ -f "$VIDEO_DIR/$stem.$ext" ] && { has_video=1; break; }
        done
        if [ -n "$has_video" ]; then
            status="  ${GREEN}✓ video ada${RESET}"
        else
            status="  ${RED}✗ video tidak ada${RESET}"
        fi
        [ -f "$MKV_OUT_DIR/$stem.mkv" ] && status+="  ${DIM}MKV sudah ada${RESET}"
        printf "  ${BOLD}%d${RESET}) %s%s\n" $((i + 1)) "$base" "$status"
    done
    echo ""
    echo -ne "  Nomor (contoh: 1,3,7 atau 1-5, kosong=semua): "
    read -r selected </dev/tty

    local -a chosen=()
    if [ -n "$selected" ]; then
        local part idx
        IFS=',' read -ra parts <<< "$selected"
        for part in "${parts[@]}"; do
            part="${part// /}"
            if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
                local a=${BASH_REMATCH[1]} b=${BASH_REMATCH[2]}
                for ((i = a; i <= b; i++)); do
                    idx=$((i - 1))
                    [ "$idx" -ge 0 ] && [ "$idx" -lt "${#srt_list[@]}" ] && chosen+=("${srt_list[$idx]}")
                done
            elif [[ "$part" =~ ^[0-9]+$ ]]; then
                idx=$((part - 1))
                [ "$idx" -ge 0 ] && [ "$idx" -lt "${#srt_list[@]}" ] && chosen+=("${srt_list[$idx]}")
            fi
        done
    else
        chosen=("${srt_list[@]}")
    fi

    if [ "${#chosen[@]}" -eq 0 ]; then
        warn "Tidak ada SRT yang dipilih."
        return 1
    fi
    SELECTED_SRTS=("${chosen[@]}")
    return 0
}

# ─── Mux MKV softsub dari SRT pilihan ───────────────────────────────────────────
mux_selected() {
    select_srt_files || return 0
    mkdir -p "$MKV_OUT_DIR"
    local count=0
    local -a cleaned_stems=()
    local srt_file
    for srt_file in "${SELECTED_SRTS[@]}"; do
        local base stem found_video ext
        base="$(basename "$srt_file")"
        stem="${base%_ID.srt}"
        found_video=""
        for ext in mp4 mkv avi mov webm flv; do
            candidate="$VIDEO_DIR/$stem.$ext"
            [ -f "$candidate" ] && { found_video="$candidate"; break; }
        done

        if [ -z "$found_video" ]; then
            warn "Video tidak ditemukan untuk $stem — skip"
            continue
        fi

        local mkv_out="$MKV_OUT_DIR/$stem.mkv"
        if [ -f "$mkv_out" ] && [ "$FORCE" != "1" ]; then
            info "MKV sudah ada: $mkv_out (skip)"
            continue
        fi

        run_with_spinner "Mux $stem" \
            ffmpeg -y -i "$found_video" -i "$srt_file" \
                -map 0:v -map 0:a -map 1 \
                -c:v copy -c:a copy -c:s srt \
                -metadata:s:2 language=ind -metadata:s:2 title="Indonesia" \
                -disposition:s:2 default \
                "$mkv_out"
        if [ $? -eq 0 ] && [ -s "$mkv_out" ]; then
            ok "MKV: $mkv_out"
            cleaned_stems+=("$stem")
            ((count++))
        else
            warn "Mux gagal untuk $stem"
        fi
    done

    echo ""
    if [ "$count" -eq 0 ]; then
        warn "Tidak ada MKV yang dibuat."
        return
    fi

    ok "Mux selesai: $count MKV."

    # Auto cleanup: hapus video source + SRT di srt/subtitles + whisper cache di whisper/transcripts
    local cleaned=0 stem
    for stem in "${cleaned_stems[@]}"; do
        local ext
        for ext in mp4 mkv avi mov webm flv; do
            candidate="$VIDEO_DIR/$stem.$ext"
            [ -f "$candidate" ] && { to_trash "$candidate"; break; }
        done
        to_trash "$SRT_OUTPUT_DIR/${stem}_ID.srt"
        to_trash "$WHISPER_SRT_DIR/${stem}.srt"
        to_trash "$WHISPER_SRT_DIR/${stem}.lang"
        ((cleaned++))
    done
    [ "$cleaned" -gt 0 ] && ok "Clean: $cleaned video + SRT + whisper cache dikirim ke trash."
}

# ─── Scan konteks (QA proofread) SRT pilihan ───────────────────────────────────
scan_context() {
    select_srt_files || return 0
    local srt_file
    for srt_file in "${SELECTED_SRTS[@]}"; do
        echo -e "  ${CYAN}►${RESET}  Scan konteks: $(basename "$srt_file")"
        run_in_venv "$SRT_DIR" python3 srt.py --proofread --input "$srt_file" --yes $SRT_EXTRA
    done
}

# ─────────────────────────────────────────────────────────────────────────────

# ponytail: --help / --dry-run bypass menu (non-interaktif, tidak butuh /dev/tty)
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            echo "Usage: ./sub.sh [video ...] [--verbose|-v] [--dry-run] [--json-log[=FILE]] [--help]"
            echo "  Pipeline: transcribe (Groq Whisper) → translate (Groq GPT-OSS) → MKV"
            echo "  Menu 7 = ketik path manual (satu per baris, dukung glob ~/V/*.mp4 + folder)"
            echo "  ENV: MKV=1 CLEAN=1 AUTO=1 FORCE=1 SUB_LANG=ja|zh|en|ko|th|tl|ru|auto VERBOSE=1 DRY_RUN=1 JSON_LOG=path"
            exit 0
            ;;
    esac
done

# ── Mode TUI non-interaktif (tui.py: TUI_MODE=mux|scan + TUI_SRTS newline-separated)
# ponytail: dispatch di bawah reuse mux_selected/scan_context apa adanya (single source of truth ffmpeg/proofread)
if [ "${TUI_MODE:-}" = "mux" ]; then
    MODE_MUX_SELECT=1
elif [ "${TUI_MODE:-}" = "scan" ]; then
    MODE_SCAN_CONTEXT=1
fi
# ── Menu interaktif (kalau tidak ada env var mode yang diset) ──────────────
if [ "$DRY_RUN" != "1" ] && [ "$MKV" != "1" ] && [ "$CLEAN" != "1" ] && [ "$AUTO" != "1" ] && [ -z "${TUI_MODE:-}" ]; then
    show_menu
    if [ "${MODE_MUX_SELECT:-0}" = "1" ]; then
        mux_selected
        exit 0
    fi
    if [ "${MODE_SCAN_CONTEXT:-0}" = "1" ]; then
        scan_context
        exit 0
    fi
    # ponytail: opsi 7 isi CLI_VIDEOS biar validasi + skip filter reuse jalur argumen
    if [ "${MODE_CUSTOM_PATH:-0}" = "1" ]; then
        ask_custom_paths || exit 0
        CLI_VIDEOS=("${CUSTOM_VIDEOS[@]}")
    fi
fi

# ponytail: dispatch mode TUI (jalur interaktif sudah exit di dalam show_menu)
if [ "${MODE_MUX_SELECT:-0}" = "1" ]; then
    mux_selected
    exit 0
fi
if [ "${MODE_SCAN_CONTEXT:-0}" = "1" ]; then
    scan_context
    exit 0
fi

print_header
START_TIME=$SECONDS
# ponytail: json log header
json_log "{\"event\":\"pipeline_start\",\"ts\":$(date +%s),\"verbose\":$VERBOSE,\"dry_run\":$DRY_RUN,\"lang\":\"$SUBTITLE_LANG\",\"mkv\":$MKV,\"clean\":$CLEAN}"

validate_lang "$SUBTITLE_LANG" || fail "Bahasa tidak valid: '$SUBTITLE_LANG'. Pakai SUB_LANG=auto|ja|zh|en"

mkdir -p "$VIDEO_DIR" "$WHISPER_SRT_DIR" "$SRT_INPUT_DIR" "$SRT_OUTPUT_DIR"
[ "$MKV" = "1" ] && mkdir -p "$MKV_OUT_DIR"

# ponytail: path video eksplisit dipakai langsung; tanpa argumen → scan folder seperti dulu
if [ "${#CLI_VIDEOS[@]}" -gt 0 ]; then
    VIDEO_LIST=()
    for v in "${CLI_VIDEOS[@]}"; do
        if [ ! -f "$v" ]; then
            warn "File tidak ada, skip: $v"
            continue
        fi
        case "${v,,}" in
            *.mp4|*.mkv|*.avi|*.mov|*.webm|*.flv) VIDEO_LIST+=("$v") ;;
            *) warn "Ekstensi video tak didukung, skip: $v" ;;
        esac
    done
    [ "${#VIDEO_LIST[@]}" -eq 0 ] && fail "Tidak ada path video valid dari argumen."
else
    mapfile -d '' VIDEO_LIST < <(find "$VIDEO_DIR" -maxdepth 1 \
        \( -iname "*.mp4" -o -iname "*.mkv" -o -iname "*.avi" \
           -o -iname "*.mov" -o -iname "*.webm" -o -iname "*.flv" \) \
        -print0 2>/dev/null | sort -z)

    if [ "${#VIDEO_LIST[@]}" -eq 0 ]; then
        fail "Tidak ada video di $VIDEO_DIR"
    fi
fi

echo -e "${BOLD}${BLUE}┌─ Ditemukan ${#VIDEO_LIST[@]} video${RESET}"

# ── Filter video interaktif (hanya dari menu & tanpa path eksplisit) ─────────
if [ "${FROM_MENU:-0}" = "1" ] && [ "${#CLI_VIDEOS[@]}" -eq 0 ]; then
    echo ""
    echo -e "  ${BOLD}Pilih video yang diproses:${RESET}"
    for i in "${!VIDEO_LIST[@]}"; do
        printf "  ${BOLD}%d${RESET}) %s\n" $((i + 1)) "$(basename "${VIDEO_LIST[$i]}")"
    done
    echo ""
    echo -ne "  Nomor (contoh: 1,3,7 atau 1-5, kosong=semua): "
    read -r selected </dev/tty

    if [ -n "$selected" ]; then
        filtered=()
        IFS=',' read -ra parts <<< "$selected"
        for part in "${parts[@]}"; do
            part="${part// /}"
            if [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then
                for ((i = ${BASH_REMATCH[1]}; i <= ${BASH_REMATCH[2]}; i++)); do
                    idx=$((i - 1))
                    [ "$idx" -ge 0 ] && [ "$idx" -lt "${#VIDEO_LIST[@]}" ] && filtered+=("${VIDEO_LIST[$idx]}")
                done
            elif [[ "$part" =~ ^[0-9]+$ ]]; then
                idx=$((part - 1))
                [ "$idx" -ge 0 ] && [ "$idx" -lt "${#VIDEO_LIST[@]}" ] && filtered+=("${VIDEO_LIST[$idx]}")
            fi
        done
        # dedupe
        VIDEO_LIST=()
        for v in "${filtered[@]}"; do
            seen=0
            for existing in "${VIDEO_LIST[@]}"; do
                [ "$existing" = "$v" ] && { seen=1; break; }
            done
            [ "$seen" = 0 ] && VIDEO_LIST+=("$v")
        done
        unset filtered

        if [ "${#VIDEO_LIST[@]}" -eq 0 ]; then
            fail "Tidak ada video yang dipilih."
        fi
        echo ""
        info "Diproses: ${#VIDEO_LIST[@]} video"
    fi
fi

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

# ponytail: dry-run preview tanpa Groq
if [ "$DRY_RUN" = "1" ]; then
    info "[dry-run] Preview tanpa panggil Groq — ${#VIDEO_LIST[@]} video, $TOTAL_NEED perlu proses"
    for v in "${VIDEO_LIST[@]}"; do echo "  - $(basename "$v")"; done
    for v in "${NEED_PROCESS[@]}"; do echo "  → perlu: $(basename "$v")"; done
    # dry-run estimasi via srt --dry-run (tanpa Groq)
    for v in "${NEED_PROCESS[@]}"; do
        base="$(basename "$v")"; stem="${base%.*}"
        # whisper dry-run
        [ -f "$WHISPER_SRT_DIR/$stem.srt" ] || echo "  [dry-run] transcribe: $base → $WHISPER_SRT_DIR/$stem.srt"
    done
    json_log "{\"event\":\"dry_run\",\"total\":${#VIDEO_LIST[@]},\"need\":$TOTAL_NEED}"
    exit 0
fi

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
            json_log "{\"event\":\"video_skip\",\"video\":\"$base\",\"output\":\"$final_srt\"}"
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
        # ponytail: dry-run/verbose/json diteruskan ke python
        run_with_spinner "Transcribe" \
            run_in_venv "$WHISPER_DIR" python3 transcribe.py \
                --lang "$SUBTITLE_LANG" --video "$video" --output "$whisper_srt" $WHISPER_EXTRA
        code=$?
        if [ $code -ne 0 ] || [ ! -s "$whisper_srt" ]; then
            warn "Transcribe gagal untuk $base"
            json_log "{\"event\":\"transcribe_fail\",\"video\":\"$base\",\"code\":$code}"
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
        json_log "{\"event\":\"lang_invalid\",\"video\":\"$base\",\"lang\":\"$SRC_LANG\"}"
        ((FAILED++))
        clean_srt_input
        continue
    fi
    info "Bahasa sumber: $(lang_label "$SRC_LANG")"

    # ── 2) COPY ke SRT input (folder sudah bersih) ───────────────────────────
    cp -f "$whisper_srt" "$srt_input_srt" || {
        warn "Gagal copy SRT ke SRT input."
        json_log "{\"event\":\"copy_fail\",\"video\":\"$base\"}"
        ((FAILED++))
        continue
    }
    info "Input translate dikunci 1 file: $srt_input_srt"

    # ── 3) TRANSLATE ──────────────────────────────────────────────────────────
    info "Translate 1 SRT saja..."
    run_with_spinner "Translate" \
        run_in_venv "$SRT_DIR" python3 srt.py \
            --lang "$SRC_LANG" --input "$srt_input_srt" --output "$final_srt" $SRT_EXTRA
    code=$?
    if [ $code -ne 0 ] || [ ! -s "$final_srt" ]; then
        warn "Translate gagal untuk $stem.srt"
        json_log "{\"event\":\"translate_fail\",\"video\":\"$base\",\"code\":$code}"
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
                    # ponytail: video di luar whisper/videos bukan milik pipeline — jangan trash
                    case "$video" in
                        "$VIDEO_DIR"/*) to_trash "$video" ;;
                        *) info "CLEAN: video luar ($video) tidak di-trash; cache+SRT saja." ;;
                    esac
                    to_trash "$whisper_srt"
                    to_trash "$whisper_lang_file"
                    to_trash "$final_srt"
                    info "CLEAN: video + SRT dikirim ke trash."
                fi
            else
                warn "Mux gagal untuk $stem"
                json_log "{\"event\":\"mux_fail\",\"video\":\"$base\"}"
            fi
        fi
    fi

    ok "Selesai: $final_srt"
    json_log "{\"event\":\"video_done\",\"video\":\"$base\",\"lang\":\"$SRC_LANG\",\"output\":\"$final_srt\",\"success\":1}"
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

# ponytail: TUI_QUIET — ringkasan 1 baris (layar selesai TUI gantikan box)
if [ -n "${TUI_QUIET:-}" ]; then
    echo "SELESAI OK=$SUCCESS SKIP=$SKIPPED FAIL=$FAILED ${MINS}m${SECS}s"
else
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
fi
json_log "{\"event\":\"pipeline_end\",\"success\":$SUCCESS,\"skip\":$SKIPPED,\"fail\":$FAILED,\"elapsed\":$ELAPSED}"

[ "$FAILED" -gt 0 ] && exit 1
exit 0
