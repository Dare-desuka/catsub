#!/usr/bin/env python3
"""
Groq Whisper Transcriber — Multi-Language Input
Input  : video (Jepang / China / Inggris)
Output : SRT file (transkripsi bahasa sumber)
"""
import os
import re
import sys
import time
import argparse
import subprocess
import json
import math
import tempfile
import traceback
from pathlib import Path

# ponytail: verbose/json/dry-run/sync-shift globals — set dari args, dipakai helper tanpa ubah signature banyak
VERBOSE = False
JSON_LOG = None
DRY_RUN = False
SYNC_SHIFT = 0.0

def _jlog(event: dict):
    if JSON_LOG:
        try:
            Path(JSON_LOG).parent.mkdir(parents=True, exist_ok=True)
            with open(JSON_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import groq_util as gu

# ── KONFIGURASI ──────────────────────────────
GROQ_API_KEYS = []

BASE_DIR = Path(__file__).resolve().parent
INPUT_FOLDER = str(BASE_DIR / "videos")
OUTPUT_FOLDER = str(BASE_DIR / "transcripts")

MODEL = "whisper-large-v3"
CHUNK_MINUTES = 10
OVERLAP_SEC = 3

MERGE_GAP_SEC = 0.0
MERGE_MAX_SEC = 4.0
MERGE_MAX_CHARS = 40

# ── KONFIGURASI SYNC (snap ke onset speech) ──
# ponytail: Groq terbukti (2026-09-22, probe 30s EN) mengembalikan 0 words walau
# diminta timestamp_granularities word — jadi snap pakai VAD ffmpeg (dep yang
# sudah ada), bukan word timestamps. whisper-timestamped lokal di-skip: butuh
# torch + model ~3GB, X280 tanpa GPU tidak kuat (0.1-0.2x realtime).
SPEECH_SNAP_WINDOW = 1.5   # cari onset/offset speech sejauh ini di luar batas segmen
SPEECH_NOISE_DB = -30.0    # di bawah level ini = sunyi
SPEECH_MIN_SILENCE = 0.3   # jeda sunyi minimal yang dianggap batas ucapan
FALLBACK_LEAD_TRIM = 0.3   # VAD gagal/tak ada match: pangkas awal segmen segini
FALLBACK_TRAIL_TRIM = 0.15 # ...dan akhir segmen segini (Whisper padding longgar)
SPLIT_MIN_SILENCE = 1.5    # jeda sunyi sepanjang ini di dalam blok → pecah 2 baris

# ── KONFIGURASI MULTI-BAHASA INPUT ───────────

LANG_CONFIGS = {
    "ja": {
        "label": "\u65e5\u672c\u8a9e",
        "whisper_lang": "ja",
        "suffix": "_JA",
        "fillers": {
            "\u3042","\u3044","\u3046","\u3048","\u304a",
            "\u3042\u30fc","\u3044\u30fc","\u3046\u30fc","\u3048\u30fc","\u304a\u30fc",
            "\u3042\u3063","\u3046\u3063","\u3048\u3063","\u304a\u3063","\u3093\u3063","\u306f\u3063","\u307b\u3063","\u3075\u3063",
            "\u3093","\u3093\u3093","\u3093\u30fc","\u3093\u3093\u3093",
            "\u306f","\u306f\u306f","\u306f\u306f\u306f","\u3048","\u3048\u30fc",
            "\u308f","\u308f\u30fc","\u308f\u3063","\u306a","\u306a\u30fc","\u3082","\u3082\u30fc","\u306d","\u306d\u30fc",
            "\u3042\u30fc\u3093","\u3046\u30fc\u3093","\u3093\u30fc\u3093","\u306f\u3042","\u307b\u304a","\u3075\u3046","\u3075\u3045",
            "\u3042\u306f","\u3046\u306f","\u3042\u3072","\u3042\u308f","\u308f\u308f","\u3072\u3083","\u3072\u3083\u30fc",
            "\u3042\u3041","\u3042\u3042","\u3042\u3042\u3042","\u3042\u30fc\u3042","\u3042\u3063\u3042\u3063",
            "\u3042\u3093","\u3042\u3041\u3093","\u3042\u30fc\u3093","\u3042\u3093\u3063",
            "\u306f\u3041","\u306f\u3042","\u306f\u3042\u3063","\u306f\u3041\u306f\u3041","\u306f\u3042\u306f\u3042","\u306f\u3063\u306f\u3063",
            "\u3075\u3041","\u3075\u3041\u3041","\u3075\u3045","\u3075\u3046","\u3075\u30fc","\u3075\u3063",
            "\u3072\u3043","\u3072\u3044","\u3046\u3045","\u3046\u3046","\u3046\u3063\u3046\u3063",
            "\u3093\u3041","\u3093\u3042","\u3093\u3093\u3063","\u3093\u3063\u3093\u3063","\u3044\u3084\u3093","\u3084\u3041","\u3084\u3042",
            "ah","ahh","aah","uh","uhh","um","umm","hmm","hm","mm","mmm","oh","ohh",
            "ha","hah","haha","eh","ehh",
        },
        "halluc_exact": gu.HALLUC_JA_EXACT,
        "halluc_contains": gu.HALLUC_JA_CONTAINS,
        "char_re": re.compile(r'[\u3040-\u309f\u30a0-\u30ff\u3400-\u9fff\uff00-\uffef]'),
        # ponytail: initial_prompt sbg bias ejaan — kata yang sering salah dengar
        # (homofon/dominan adegan dewasa). Whisper pakai ini buat "mengeja" vocab
        # yang jarang, bukan cuma konteks. <=224 token.
        "prompt": ("チンポ ちんぽ チンコ ちんこ おちんちん おちんちん ペニス "
                   "おまんこ まんこ オシュレット オシュレト イク いくいく メルメル "
                   "うん ああ はぁ んっ ふぅ いいよ きもちいい いっちゃう"),
    },
    "zh": {
        "label": "\u4e2d\u6587",
        "whisper_lang": "zh",
        "suffix": "_ZH",
        "fillers": {
            "\u5617","\u554a","\u54e6","\u54ce","\u5443","\u54c8","\u5582","\u54df","\u5509",
            "\u5617\u5617","\u554a\u554a","\u5443\u5443","\u54e6\u54e6",
            "um","uh","hmm","hm","ah","oh","eh",
        },
        "halluc_exact": gu.HALLUC_ZH_EXACT,
        "halluc_contains": gu.HALLUC_ZH_CONTAINS,
        "char_re": re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]'),
        # ponytail: bias ejaan zh ≤224 token — homofon vulgar + desah umum, biar Whisper tidak mishear
        "prompt": "鸡巴 阴茎 屌 鸡鸡 屄 阴道 逼 高潮 射精 做爱 性交 呻吟 嗯 啊 哦 哈 喔 呜",
    },
    "en": {
        "label": "English",
        "whisper_lang": "en",
        "suffix": "_EN",
        "fillers": {
            "um","uh","uhh","hmm","hm","mm","mmm",
            "ah","ahh","oh","ohh","eh","ehh",
            "like",
        },
        "halluc_exact": gu.HALLUC_EN_EXACT,
        "halluc_contains": gu.HALLUC_EN_CONTAINS,
        "char_re": re.compile(r'[a-zA-Z]'),
        # ponytail: bias ejaan en ≤224 token — homofon vulgar + interjeksi umum
        "prompt": "penis cock dick pussy vagina cum fuck moan groan oh ah um stick",
    },
    "ko": {
        "label": "한국어",
        "whisper_lang": "ko",
        "suffix": "_KO",
        "fillers": {
            "아", "어", "음", "응", "흠", "아아", "어어",
            "음음", "흐", "헉", "하", "허", "아이고",
            "um", "uh", "ah", "oh",
        },
        "halluc_exact": gu.HALLUC_KO_EXACT,
        "halluc_contains": gu.HALLUC_KO_CONTAINS,
        "char_re": re.compile(r'[\uac00-\ud7a3]'),
        # ponytail: bias ejaan ko ≤224 token — homofon vulgar + desah umum
        "prompt": "자지 고추 보지 클리토리스 오르가즘 사정 섹스 신음 아 어 응 흐 헉 하",
    },
    "th": {
        "label": "ไทย",
        "whisper_lang": "th",
        "suffix": "_TH",
        "fillers": set(),
        "halluc_exact": set(),
        "halluc_contains": set(),
        "char_re": re.compile(r'[\u0e00-\u0e7f]'),
    },
    "tl": {
        "label": "Filipino",
        "whisper_lang": "tl",
        "suffix": "_TL",
        "fillers": set(),
        "halluc_exact": set(),
        "halluc_contains": set(),
        "char_re": re.compile(r'[a-zA-ZñÑ]'),
    },
    "ru": {
        "label": "Русский",
        "whisper_lang": "ru",
        "suffix": "_RU",
        "fillers": set(),
        "halluc_exact": set(),
        "halluc_contains": set(),
        "char_re": re.compile(r'[\u0400-\u04ff]'),
    },
}

LANG_ALIASES = {
    "ja": "ja", "japanese": "ja", "japan": "ja", "\u65e5\u672c\u8a9e": "ja",
    "zh": "zh", "chinese": "zh", "mandarin": "zh", "\u4e2d\u6587": "zh", "cmn": "zh",
    "en": "en", "eng": "en", "english": "en",
    "ko": "ko", "korean": "ko", "korea": "ko", "한국어": "ko",
    "th": "th", "thai": "th", "thailand": "th", "ไทย": "th",
    "tl": "tl", "fil": "tl", "filipino": "tl", "tagalog": "tl", "philippines": "tl",
    "ru": "ru", "rus": "ru", "russian": "ru", "rusia": "ru", "русский": "ru",
}


# ── BANNER & UTILS ──────────────────────────

def banner(rotator: gu.KeyRotator, lang: str):
    cfg = LANG_CONFIGS.get(lang)
    label = cfg["label"] if cfg else "auto-detect"
    # ponytail: TUI_QUIET — 1 baris ringkas (panel TUI gantikan box+daftar key)
    if os.environ.get("TUI_QUIET"):
        print(f"transcribe {MODEL} · {label} · {len(rotator)} key", flush=True)
        return
    flag = cfg["label"][:1] if cfg else "?"
    print(f"""
{gu.CY}{gu.B}\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  \U0001f3a7  Groq Whisper Transcriber               \u2551
\u2551  Model  : {MODEL}  \u00b7 API Mode    \u2551
\u2551  Input  : {label:<31}\u2551
\u2551  Keys   : {len(rotator)}/{len(rotator)} aktif{' ' * 27}\u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d{gu.R}
""")
    for i, key in enumerate(rotator.keys, 1):
        masked = key[:10] + "..." + key[-4:]
        print(f"   {gu.DM}{i}. {masked}{gu.R}")
    print()


def fmt_dur(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds//60)}m {int(seconds%60)}s"


def get_video_duration(video_path: Path) -> float:
    probe = None
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        if VERBOSE and probe.stderr:
            print(f"   {gu.DM}[verbose] ffprobe stderr: {probe.stderr[:200]!r}{gu.R}")
        info = json.loads(probe.stdout)
        return float(info["format"]["duration"])
    except Exception as e:
        if VERBOSE:
            print(f"   {gu.YL}[verbose] ffprobe fail: {e} {probe.stdout[:100] if probe and probe.stdout else ''!r}{gu.R}")
            traceback.print_exc()
        _jlog({"event":"ffprobe_fail","video":str(video_path),"error":str(e)})
        return None


def extract_audio_chunk(video_path: Path, start_sec: float, duration_sec: float, out_path: Path) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_sec), "-i", str(video_path),
        "-t", str(duration_sec), "-vn",
        "-af", "highpass=f=80,afftdn=nf=-20",
        "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-q:a", "3",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    return result.returncode == 0


def chunk_is_silent(audio_path: Path, thresh_db: float = -50.0) -> bool:
    # ponytail: VAD murah via ffmpeg volumedetect — chunk tanpa suara dilewati
    # supaya Whisper tidak pernah melihat non-speech (pemicu halusinasi utama).
    # Naikkan thresh_db kalau ada suara asli yang ikut terbuang.
    cmd = ["ffmpeg", "-i", str(audio_path), "-af", "volumedetect", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False
    m = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", r.stderr)
    if not m:
        return "max_volume: -inf" in r.stderr  # sunyi digital total
    return float(m.group(1)) < thresh_db


# ── FILTER NOISE ─────────────────────────────

def _tokenize(text: str) -> list:
    tokens = re.split(r"[\u3001\uff0c,\s\u3000]+", text.strip())
    return [
        re.sub(r"[\u3002\uff0c?.!?\u2026\u3063\u30c3\u30fc\uff5e\u301c\s]+$", "", t).strip().lower()
        for t in tokens if t.strip()
    ]


def is_noise_segment(text: str, lang: str) -> bool:
    from collections import Counter
    cfg = LANG_CONFIGS[lang]
    fillers = cfg["fillers"]
    clean = re.sub(r"[\u3002\u3001\uff0c?.!?\u2026\s\u30fb\u30fc\uff5e\u301c\u300c\u300d\u300e\u300f()\uff08\uff09\-–\uff0d]+", "", text).strip()
    if not clean:
        return True
    if clean.lower() in gu.BREATH_EXACT:
        return True
    if len(clean) > 2 and gu.BREATH_CHARS_RE.fullmatch(clean):
        return True
    if len(clean) <= 2 and not cfg["char_re"].search(clean):
        return True
    # ponytail: char-run garble >3 char saja — "いい"/"ああ" len 2-3 biar lolos (kata legit)
    char_count = Counter(clean)
    if len(clean) > 3 and char_count.most_common(1)[0][1] / len(clean) >= 0.55:
        return True
    tokens = _tokenize(text)
    if not tokens:
        return True
    filler_count = sum(1 for t in tokens if t in fillers)
    if filler_count / len(tokens) >= 1.0:
        return True
    return False


def clean_inline_fillers(text: str, lang: str) -> str:
    cfg = LANG_CONFIGS[lang]
    fillers = cfg["fillers"]
    sentences = re.split(r'(?<=[\u3002\uff0c!.?])', text)
    cleaned_parts = []
    for sent in sentences:
        tokens = _tokenize(sent)
        if not tokens:
            continue
        kept = [t for t in tokens if t not in fillers]
        if kept:
            part = "\u3001".join(kept) if lang == "ja" else " ".join(kept)
            m = re.search(r'[\u3002\uff0c!.?]$', sent.strip())
            if m:
                part += m.group()
            cleaned_parts.append(part)
    sep = "\u3000" if lang == "ja" else " "
    result = sep.join(cleaned_parts).strip()
    result = re.sub(r'^[\u3001\uff0c,\s\u3002\uff0c?\u2026\u3000]+', '', result).strip()
    return result


def filter_noise(blocks: list, lang: str) -> list:
    result = []
    for b in blocks:
        cleaned = clean_inline_fillers(b["text"], lang)
        if not cleaned or is_noise_segment(cleaned, lang):
            continue
        b = dict(b)
        b["text"] = cleaned
        result.append(b)
    return result


# ── FILTER HALUSINASI ────────────────────────

def is_hallucination(text: str, duration_sec: float, lang: str) -> tuple:
    cfg = LANG_CONFIGS[lang]
    t = text.strip()
    if t in cfg["halluc_exact"]:
        return True, f"exact hallucination: {t!r}"
    for frag in cfg["halluc_contains"]:
        if frag in t:
            return True, f"contains hallucination: {frag!r}"
    rep = gu.repeated_phrase(t)
    if rep:
        return True, f"frasa berulang: {rep[:30]!r}"
    if len(t) <= 2 and not cfg["char_re"].search(t):
        return True, f"terlalu pendek tanpa karakter {lang}: {t!r}"
    if lang != "en":
        reason = gu.check_latin_junk(t, cfg["char_re"], lang)
        if reason:
            return True, reason
    if duration_sec >= 20.0 and len(t) <= 25:
        return True, f"teks pendek ({len(t)} char) di segment panjang ({duration_sec:.0f}s)"
    return False, ""


def is_low_confidence(block: dict) -> tuple:
    """Sinyal confidence internal Whisper dari verbose_json. Ini VAD + halusinasi
    detection Whisper sendiri: no_speech_prob tinggi + logprob rendah = segment
    tidak ada ucapan (musik/desahan/diam) yang diisi narasi halusinasi.
    Threshold = default OpenAI whisper decode (no_speech_threshold=0.6,
    logprob_threshold=-1.0, compression_ratio_threshold=2.4)."""
    nsp = block.get("no_speech_prob")
    alp = block.get("avg_logprob")
    cr = block.get("compression_ratio")
    # ponytail: nsp>0.85 saja cukup — model pede dgn lirik khayalan musik
    # (SONE-970: BGM nsp=0.87/alp=-0.26 lolos AND; dialog asli nsp=0.16)
    if nsp is not None and nsp > 0.85:
        return True, f"no-speech (nsp={nsp:.2f})"
    if nsp is not None and alp is not None and nsp > 0.6 and alp < -1.0:
        return True, f"no-speech (nsp={nsp:.2f}, logprob={alp:.2f})"
    if cr is not None and cr > 2.4:
        return True, f"compression-ratio tinggi ({cr:.2f})"
    return False, ""


# ── POST-PROCESSING ──────────────────────────

def deduplicate_blocks(blocks: list) -> list:
    if not blocks:
        return blocks
    def norm(t):
        return re.sub(r"[\s\u3002\u3001\uff0c,.!?\u2026]+", "", t).lower()
    result = [blocks[0]]
    for b in blocks[1:]:
        prev = result[-1]
        time_gap = b["start"] - prev["end"]
        norm_b = norm(b["text"])
        norm_p = norm(prev["text"])
        if norm_b == norm_p and time_gap < OVERLAP_SEC * 2:
            continue
        if time_gap < OVERLAP_SEC * 2 and len(norm_b) > 3:
            if norm_b in norm_p or norm_p in norm_b:
                if len(norm_b) > len(norm_p):
                    result[-1] = b
                continue
        result.append(b)
    return result


def split_long_segments(blocks: list, lang: str, max_chars: int = 45) -> list:
    if lang == "ja":
        split_re = re.compile(r'(?<=[\u3002\u3001])')   # 。、
    elif lang == "zh":
        split_re = re.compile(r'(?<=[\u3002\uff0c\u3001])')  # 。，、
    else:
        split_re = re.compile(r'(?<=[.!?])\s+')
    result = []
    for b in blocks:
        text = b["text"].strip()
        duration = b["end"] - b["start"]
        sentences = [s.strip() for s in split_re.split(text) if s.strip()]
        if len(sentences) <= 1 or len(text) <= max_chars:
            result.append(b)
            continue
        # ponytail: Groq terbukti tak kirim word-timestamps (probe 2026-09-22: 0 words)
        # → bagi durasi proporsional thd panjang karakter tiap kalimat. Approx,
        # tapi jauh lebih baik dari blok panjang menempel.
        total_chars = sum(len(re.sub(r'\s+', '', s)) for s in sentences)
        if total_chars <= 0:
            result.append(b)
            continue
        cursor = b["start"]
        for si, sent in enumerate(sentences):
            is_last = (si == len(sentences) - 1)
            seg_dur = (duration * len(re.sub(r'\s+', '', sent)) / total_chars
                       if not is_last else b["end"] - cursor)
            end = cursor + seg_dur
            result.append({"start": round(cursor, 3), "end": round(end, 3), "text": sent})
            cursor = end
    return result


def merge_short_segments(blocks: list) -> list:
    if not blocks:
        return blocks
    merged = [dict(blocks[0])]
    for b in blocks[1:]:
        last = merged[-1]
        gap = b["start"] - last["end"]
        new_dur = b["end"] - last["start"]
        new_text = re.sub(r"  +", " ", last["text"] + " " + b["text"]).strip()
        if gap <= MERGE_GAP_SEC and new_dur <= MERGE_MAX_SEC and len(new_text) <= MERGE_MAX_CHARS:
            last["end"] = b["end"]
            last["text"] = new_text
        else:
            merged.append(dict(b))
    return merged


def fix_timestamps(blocks: list) -> list:
    fixed = []
    for b in blocks:
        b = dict(b)
        if b["end"] <= b["start"]:
            b["end"] = b["start"] + 1.5
        if (b["end"] - b["start"]) < 0.3:
            b["end"] = b["start"] + 0.3
        fixed.append(b)
    for i in range(len(fixed) - 1):
        if fixed[i]["end"] > fixed[i + 1]["start"]:
            fixed[i]["end"] = round(fixed[i + 1]["start"] - 0.05, 3)
            if fixed[i]["end"] <= fixed[i]["start"]:
                fixed[i]["end"] = round(fixed[i]["start"] + 0.1, 3)
    return fixed


def get_speech_intervals(video_path: Path, duration: float) -> list:
    # ponytail: VAD murah via ffmpeg silencedetect — 1 pass decode seluruh file
    # (~1 mnt per jam audio di CPU), tanpa dep/ML baru. Gagal → [] (fallback trim).
    cmd = ["ffmpeg", "-i", str(video_path), "-af",
           f"silencedetect=noise={SPEECH_NOISE_DB}dB:d={SPEECH_MIN_SILENCE}",
           "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return []
    events = re.findall(r"silence_(start|end):\s*([\d.]+)", r.stderr)
    silences, open_start = [], None
    for kind, ts in events:  # ponytail: parse urut biar start tanpa end tidak geser pasangan
        if kind == "start":
            open_start = float(ts)
        elif open_start is not None:
            silences.append((open_start, float(ts)))
            open_start = None
    if open_start is not None:  # sunyi sampai EOF (tanpa silence_end)
        silences.append((open_start, duration))
    speech, cursor = [], 0.0
    for s, e in sorted(silences):
        if s > cursor:
            speech.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        speech.append((cursor, duration))
    return speech


def snap_to_speech(blocks: list, speech: list) -> list:
    # ponytail: start segmen Whisper longgar (padding sunyi) → sub muncul duluan.
    # Jepit tiap blok ke onset/offset speech terdekat dalam jendela; lead -0.1s
    # biar ada napas, trail +0.15s biar kebaca. Guard min-dur, overlap di fix_timestamps.
    fixed = []
    for b in blocks:
        b = dict(b)
        s, e = b["start"], b["end"]
        if speech:
            # ponytail: start hanya ke onset DI DALAM blok — interval yg nyebrang
            # batas kiri milik blok sebelumnya (jangan dicuri). Konsekuensi: blok
            # yang mulai telat di tengah ucapan tidak dikoreksi (jarang, minor).
            onsets = [a for a, z in speech if s <= a <= e]
            if onsets:
                b["start"] = round(max(0.0, min(onsets) - 0.10), 3)
            offsets = [z for a, z in speech
                       if (a <= e and z >= s) or (e <= z <= e + SPEECH_SNAP_WINDOW)]
            if offsets:
                b["end"] = round(max(offsets) + 0.15, 3)
        else:
            b["start"], b["end"] = round(s + FALLBACK_LEAD_TRIM, 3), round(e - FALLBACK_TRAIL_TRIM, 3)
        if b["end"] - b["start"] < 0.3:
            b["end"] = round(b["start"] + 0.3, 3)
        if SYNC_SHIFT:
            b["start"] = round(max(0.0, b["start"] + SYNC_SHIFT), 3)
            b["end"] = round(b["end"] + SYNC_SHIFT, 3)
        fixed.append(b)
    return fixed


SENT_END_RE = re.compile(r'[。.！？!?…]+["\'」』)）】\s]*')

def _split_text_at(text: str, frac: float) -> tuple:
    # ponytail: belah teks di akhir kalimat terdekat posisi frac (tanpa word
    # timestamps = approx). Tanpa tanda baca → belah proporsional, latin di spasi.
    bounds = [m.end() for m in SENT_END_RE.finditer(text)]
    bounds = [p for p in bounds if 0 < p < len(text)]
    if bounds:
        cut = min(bounds, key=lambda p: abs(p - frac * len(text)))
    else:
        cut = int(frac * len(text))
        sp = text.rfind(" ", 0, cut)
        if sp > cut * 0.5:
            cut = sp
    return text[:cut].strip(), text[cut:].strip()


def split_on_silence(blocks: list, speech: list, min_gap: float = SPLIT_MIN_SILENCE) -> list:
    # ponytail: Whisper menelan jeda antar-ucapan/orang dalam 1 segmen (s/d 30s) →
    # pecah blok di gap terbesar ≥min_gap, rekursif. Gap di tepi (<0.5s sisi) di-skip.
    if not speech:
        return blocks
    gaps = [(a_e, b_s) for (_, a_e), (b_s, _) in zip(speech, speech[1:])
            if b_s - a_e >= min_gap]
    if not gaps:
        return blocks
    result = []
    for b in blocks:
        pieces = [dict(b)]
        while True:
            nxt, did = [], False
            for p in pieces:
                s, e = p["start"], p["end"]
                inside = [(gs, ge) for gs, ge in gaps if gs > s and ge < e]
                gs, ge = max(inside, key=lambda g: g[1] - g[0], default=(None, None))
                mid = (gs + ge) / 2 if gs is not None else None
                if mid is None or mid - s < 0.5 or e - mid < 0.5:
                    nxt.append(p)
                    continue
                t1, t2 = _split_text_at(p["text"], (mid - s) / (e - s))
                if not t1 or not t2:
                    nxt.append(p)
                    continue
                mid = round(mid, 3)
                nxt.append({"start": s, "end": mid, "text": t1})
                nxt.append({"start": mid, "end": e, "text": t2})
                did = True
            pieces = nxt
            if not did:
                break
        result.extend(pieces)
    return result


# ── SNAP FILE SRT YANG SUDAH ADA (opsi 1: tanpa Groq) ──

SNAP_SUFFIXES = ("_JA", "_ZH", "_EN", "_KO", "_TH", "_TL", "_RU", "_ID")

def parse_srt_file(path: Path) -> list:
    # ponytail: parser SRT minimal lokal (start/end → detik) — tanpa import srt.py
    # biar kedua tool tidak saling gantung.
    def to_sec(ts: str) -> float:
        h, m, s = ts.strip().replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    blocks = []
    content = path.read_text(encoding="utf-8", errors="replace")
    for raw in re.split(r"\n\s*\n", content.strip()):
        lines = raw.strip().splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        try:
            start, end = (to_sec(x) for x in lines[1].split("-->"))
        except ValueError:
            continue
        text = "\n".join(lines[2:]).strip()
        if text:
            blocks.append({"start": start, "end": end, "text": text})
    return blocks


def guess_video(srt_path: Path) -> Path | None:
    # ponytail: tebak video pendamping dari stem (buang suffix _JA/_ID/…) + ekstensi umum
    stem = srt_path.stem
    for suf in SNAP_SUFFIXES:
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
            break
    for ext in (".mp4", ".mkv", ".avi", ".webm", ".m4v"):
        cand = Path(INPUT_FOLDER) / f"{stem}{ext}"
        if cand.exists():
            return cand
    return None


def guess_lang(srt_path: Path, blocks: list) -> str:
    # ponytail: bahasa buat split kalimat fallback — urutan: sidecar .lang (eksak),
    # suffix file (_JA/…), isi teks (kana→ja). _ID → generik latin (cocok utk Indonesia).
    lang_file = srt_path.with_suffix(".lang")
    if lang_file.exists():
        code = lang_file.read_text(encoding="utf-8", errors="replace").strip().lower()
        if code in LANG_CONFIGS:
            return code
    stem = srt_path.stem
    for suf in SNAP_SUFFIXES:
        if stem.endswith(suf):
            code = suf[1:].lower()
            return code if code in LANG_CONFIGS else "other"
    sample = " ".join(b["text"] for b in blocks[:20])
    return infer_language(sample) or "other"


def snap_file(srt_path: Path, video_path: Path | None) -> dict:
    blocks = parse_srt_file(srt_path)
    if not blocks:
        print(f"   {gu.RD}✘  {srt_path.name}: tidak ada blok terbaca — tulis dibatalkan{gu.R}")
        return {}
    speech = []
    if video_path:
        duration = get_video_duration(video_path) or 0.0
        speech = get_speech_intervals(video_path, duration)
    def process(bs: list) -> list:
        # ponytail: rantai sama dgn pipeline (snap → belah → fix) biar hasil konsisten
        out = snap_to_speech(bs, speech)
        if speech:
            out = split_on_silence(out, speech)
        else:  # tanpa VAD: pecah blok kalimat panjang (aproksimasi jeda antar-ucapan)
            out = split_long_segments(out, guess_lang(srt_path, bs))
        return fix_timestamps(out)
    if speech:
        print(f"   {gu.DM}VAD: {len(speech)} rentang speech — {srt_path.name}{gu.R}")
    else:
        print(f"   {gu.YL}!  Tanpa video/VAD ({srt_path.name}) — fallback trim + belah kalimat{gu.R}")
    if DRY_RUN:
        preview = process([dict(b) for b in blocks])
        for b in preview[:3]:
            print(f"   {gu.DM}{b['start']:.2f}-{b['end']:.2f}  {b['text'][:40]!r}{gu.R}")
        print(f"   {gu.DM}[dry-run] {len(blocks)}→{len(preview)} blok, tulis dibatalkan{gu.R}")
        return {"file": srt_path.name, "segments": len(preview), "dry_run": True}
    snapped = process(blocks)
    gu.write_srt(snapped, srt_path)
    moved = sum(1 for a, b in zip(blocks, snapped) if abs(a["start"] - b["start"]) > 0.005) \
        if len(blocks) == len(snapped) else len(snapped)
    print(f"   {gu.GR}✔  {srt_path.name}: {len(blocks)}→{len(snapped)} blok, {moved} start bergeser/pecah{gu.R}")
    return {"file": srt_path.name, "segments": len(snapped), "moved": moved,
            "vad": bool(speech)}


# ── GROQ API ─────────────────────────────────

def transcribe_chunk(rotator: gu.KeyRotator, audio_path: Path, chunk_offset: float,
                     lang: str, retry: int = 3) -> list:
    cfg = LANG_CONFIGS[lang]
    client, key_idx = rotator.current()
    for attempt in range(1, retry + 1):
        try:
            with open(audio_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    file=(audio_path.name, f, "audio/mpeg"),
                    model=MODEL,
                    language=cfg["whisper_lang"],
                    response_format="verbose_json",
                    timestamp_granularities=["segment", "word"],
                    temperature=0.0,
                    prompt=cfg.get("prompt") or None,
                )
            segments = response.segments or []
            blocks = []
            for seg in segments:
                text = (seg["text"] if isinstance(seg, dict) else seg.text).strip()
                start = (seg["start"] if isinstance(seg, dict) else seg.start)
                end = (seg["end"] if isinstance(seg, dict) else seg.end)
                if not text:
                    continue
                # ponytail: metadata confidence Whisper (avg_logprob / no_speech_prob /
                # compression_ratio) — dipakai filter halusinasi berbasis sinyal, bukan
                # frasa whack-a-mole.
                if isinstance(seg, dict):
                    nsp, alp, cr = seg.get("no_speech_prob"), seg.get("avg_logprob"), seg.get("compression_ratio")
                else:
                    nsp = getattr(seg, "no_speech_prob", None)
                    alp = getattr(seg, "avg_logprob", None)
                    cr = getattr(seg, "compression_ratio", None)
                words = []
                raw_words = (
                    seg.get("words", []) if isinstance(seg, dict)
                    else getattr(seg, "words", []) or []
                )
                for w in raw_words:
                    if isinstance(w, dict):
                        w_text = w.get("word", "")
                        w_start = w.get("start", start)
                        w_end = w.get("end", end)
                    else:
                        w_text = getattr(w, "word", "")
                        w_start = getattr(w, "start", start)
                        w_end = getattr(w, "end", end)
                    if w_text:
                        words.append({"word": w_text,
                                     "start": round(w_start + chunk_offset, 3),
                                     "end": round(w_end + chunk_offset, 3)})
                blocks.append({"start": round(start + chunk_offset, 3),
                              "end": round(end + chunk_offset, 3),
                              "text": text, "words": words,
                              "no_speech_prob": nsp, "avg_logprob": alp,
                              "compression_ratio": cr})
            rotator.next()
            return blocks
        except Exception as e:
            err_str = str(e).lower()
            if VERBOSE:
                print(f"\n   {gu.YL}[verbose] Groq raw: {getattr(e,'body',e)!r} status={getattr(e,'status_code','')!r}{gu.R}", flush=True)
                traceback.print_exc()
            _jlog({"event":"groq_error","key":key_idx+1,"error":str(e),"attempt":attempt,"lang":lang})
            if "rate" in err_str or "429" in err_str:
                print(f"\n   {gu.YL}Key #{key_idx+1} rate-limit \u2014 pindah key{gu.R}", flush=True)
                rotator.mark_rate_limited(key_idx)
                client, key_idx = rotator.next()
                time.sleep(2)
            elif "401" in err_str or "invalid_api_key" in err_str:
                print(f"\n   {gu.RD}Key #{key_idx+1} invalid (401) \u2014 skip permanen{gu.R}", flush=True)
                rotator.mark_invalid(key_idx)
                client, key_idx = rotator.next()
                time.sleep(1)
            elif attempt < retry:
                print(f"\n   {gu.YL}Error: {e} \u2014 retry {attempt}/{retry}{gu.R}", flush=True)
                time.sleep(5 * attempt)
            else:
                raise
    return []


def infer_language(text: str, reported_language: str = "") -> str:
    reported = (reported_language or "").strip().lower()
    if reported in LANG_ALIASES:
        return LANG_ALIASES[reported]
    kana = len(re.findall(r"[\u3040-\u30ff]", text))
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if kana:
        return "ja"
    if cjk:
        return "zh"
    if latin:
        return "en"
    return None


def detect_language_from_audio(rotator: gu.KeyRotator, audio_path: Path, retry: int = 3) -> tuple:
    client, key_idx = rotator.current()
    for attempt in range(1, retry + 1):
        try:
            with open(audio_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    file=(audio_path.name, f, "audio/mpeg"),
                    model=MODEL,
                    response_format="verbose_json",
                    timestamp_granularities=["segment", "word"],
                    temperature=0.0,
                )
            segments = getattr(response, "segments", None) or []
            texts = []
            for seg in segments:
                text = (seg.get("text", "") if isinstance(seg, dict) else getattr(seg, "text", "")).strip()
                if text:
                    texts.append(text)
            text = " ".join(texts).strip()
            reported = getattr(response, "language", "") or ""
            lang = infer_language(text, reported)
            rotator.next()
            return lang, text, reported
        except Exception as e:
            err_str = str(e).lower()
            if VERBOSE:
                print(f"   {gu.YL}[verbose] detect Groq raw: {getattr(e,'body',e)!r}{gu.R}", flush=True)
                traceback.print_exc()
            _jlog({"event":"detect_error","key":key_idx+1,"error":str(e),"attempt":attempt})
            if "rate" in err_str or "429" in err_str:
                print(f"\n   {gu.YL}Key #{key_idx+1} rate-limit saat deteksi \u2014 pindah key{gu.R}", flush=True)
                rotator.mark_rate_limited(key_idx)
                client, key_idx = rotator.next()
                time.sleep(2)
            elif "401" in err_str or "invalid_api_key" in err_str:
                print(f"\n   {gu.RD}Key #{key_idx+1} invalid (401) \u2014 skip permanen{gu.R}", flush=True)
                rotator.mark_invalid(key_idx)
                client, key_idx = rotator.next()
                time.sleep(1)
            elif attempt < retry:
                print(f"\n   {gu.YL}Deteksi bahasa error: {e} \u2014 retry {attempt}/{retry}{gu.R}", flush=True)
                time.sleep(5 * attempt)
            else:
                raise
    return None, "", ""


def detect_video_language(rotator: gu.KeyRotator, video_path: Path, duration: float) -> str:
    sample_duration = min(75.0, max(20.0, duration))
    starts = [0.0]
    if duration > 180:
        starts.extend([duration * 0.25, duration * 0.5])
    print(f"   {gu.DM}Deteksi bahasa otomatis\u2026{gu.R}", flush=True)
    votes = {}
    reported_by_lang = {}
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, start_sec in enumerate(starts, start=1):
            start_sec = min(start_sec, max(0.0, duration - sample_duration))
            audio_path = Path(tmpdir) / f"detect_{idx:02d}.mp3"
            if not extract_audio_chunk(video_path, start_sec, sample_duration, audio_path):
                continue
            lang, text, reported = detect_language_from_audio(rotator, audio_path)
            audio_path.unlink(missing_ok=True)
            if lang not in LANG_CONFIGS:
                continue
            # ponytail: sample tanpa signal ucapan (sunyi/halusinasi) jangan
            # dipakai buat deteksi — salah bahasa = seluruh file salah.
            if len(text.strip()) < 20 or is_hallucination(text, sample_duration, lang)[0] \
                    or is_noise_segment(text, lang):
                print(f"   {gu.YL}!  Sampel {idx} tanpa signal ucapan jelas, coba sampel lain\u2026{gu.R}")
                continue
            votes[lang] = votes.get(lang, 0) + 1
            reported_by_lang.setdefault(lang, reported)
    if votes:
        best = max(votes, key=votes.get)
        cfg = LANG_CONFIGS[best]
        report = f", Whisper: {reported_by_lang[best]}" if reported_by_lang[best] else ""
        print(f"   {gu.GR}\u2714  Bahasa terdeteksi: {cfg['label']}{gu.DM}{report}{gu.R}")
        return best
    print(f"   {gu.YL}!  Bahasa tidak terdeteksi jelas, fallback ke Jepang.{gu.R}")
    return "ja"


# ── TRANSCRIBE VIDEO ─────────────────────────

def transcribe_video(rotator: gu.KeyRotator, video_path: Path, output_path: Path,
                     lang: str) -> dict:
    requested_lang = lang
    if requested_lang != "auto" and requested_lang not in LANG_CONFIGS:
        print(f"{gu.RD}\u2718  --lang tidak valid: '{requested_lang}'. Pilih: auto | ja | zh | en{gu.R}")
        return {}
    duration = get_video_duration(video_path)
    if not duration:
        print(f"   {gu.RD}\u2718  Tidak bisa baca durasi \u2014 ffprobe error{gu.R}")
        _jlog({"event":"transcribe_fail","video":str(video_path),"reason":"ffprobe"})
        return {}
    # ponytail: dry-run — preview tanpa Groq
    if DRY_RUN:
        chunk_sec = CHUNK_MINUTES * 60
        n_chunks = math.ceil(duration / (chunk_sec - OVERLAP_SEC))
        print(f"   {gu.DM}[dry-run] {video_path.name} {fmt_dur(duration)} → {n_chunks} chunk x {CHUNK_MINUTES}m (skip Groq){gu.R}")
        _jlog({"event":"transcribe_dry_run","video":str(video_path),"duration":duration,"chunks":n_chunks,"lang":lang})
        return {"file": video_path.name, "segments": 0, "language": lang, "duration": duration, "elapsed": 0, "dry_run": True}
    if requested_lang == "auto":
        lang = detect_video_language(rotator, video_path, duration)
    cfg = LANG_CONFIGS[lang]
    print(f"\n{gu.B}{gu.CY}\u25b6  {video_path.name}{gu.R}  {gu.DM}[{cfg['label']}]{gu.R}")
    print(f"   {gu.DM}Output \u2192 {output_path}{gu.R}")
    t_start = time.time()
    print(f"   Durasi : {fmt_dur(duration)}")
    chunk_sec = CHUNK_MINUTES * 60
    n_chunks = math.ceil(duration / (chunk_sec - OVERLAP_SEC))
    print(f"   Chunk  : {n_chunks} bagian x ~{CHUNK_MINUTES} menit")
    print(f"   Model  : {MODEL}  [key #{rotator.idx+1}]")
    print()
    _jlog({"event":"transcribe_start","video":str(video_path),"duration":duration,"chunks":n_chunks,"lang":lang})
    all_blocks = []
    silent_skipped = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for ci in range(n_chunks):
            start_sec = ci * (chunk_sec - OVERLAP_SEC)
            actual_dur = min(chunk_sec, duration - start_sec)
            if actual_dur <= 0:
                break
            _, key_idx = rotator.current()
            pct = (ci / n_chunks) * 100
            print(
                f"\r   {gu.CY}[{ci+1}/{n_chunks}]{gu.R}  "
                f"{fmt_dur(start_sec)} \u2013 {fmt_dur(start_sec + actual_dur)}"
                f"  ({pct:.0f}%)  [key #{key_idx+1}]     ",
                end="", flush=True,
            )
            audio_path = Path(tmpdir) / f"chunk_{ci:04d}.mp3"
            if not extract_audio_chunk(video_path, start_sec, actual_dur, audio_path):
                print(f"\n   {gu.RD}\u2718  ffmpeg gagal chunk {ci+1}{gu.R}")
                continue
            if chunk_is_silent(audio_path):
                silent_skipped += 1
                audio_path.unlink(missing_ok=True)
                continue
            blocks = transcribe_chunk(rotator, audio_path, start_sec, lang)
            all_blocks.extend(blocks)
            audio_path.unlink(missing_ok=True)
            if ci < n_chunks - 1:
                time.sleep(1.0)
    if silent_skipped:
        print(f"\r{gu.DM}   Skip {silent_skipped}/{n_chunks} chunk sunyi (hemat panggilan API){gu.R}")
    print(f"\r   {gu.DM}{'\u2500' * 50}{gu.R}")
    raw_count = len(all_blocks)
    print(f"   {gu.DM}Post-processing\u2026{gu.R}", end="", flush=True)
    all_blocks = deduplicate_blocks(all_blocks)
    dedup_removed = raw_count - len(all_blocks)
    filtered_out = []
    clean_blocks = []
    for b in all_blocks:
        dur = b["end"] - b["start"]
        halluc, reason = is_hallucination(b["text"], dur, lang)
        if not halluc:
            halluc, reason = is_low_confidence(b)
        if halluc:
            filtered_out.append((b["text"], reason))
        else:
            clean_blocks.append(b)
    all_blocks = clean_blocks
    before_filter = len(all_blocks)
    all_blocks = filter_noise(all_blocks, lang)
    noise_removed = before_filter - len(all_blocks)
    before_split = len(all_blocks)
    all_blocks = split_long_segments(all_blocks, lang)
    split_added = len(all_blocks) - before_split
    all_blocks = merge_short_segments(all_blocks)
    print(f"\r   {gu.DM}Snap ke onset speech (ffmpeg VAD)…{gu.R}", end="", flush=True)
    speech = get_speech_intervals(video_path, duration)
    if speech:
        print(f"\r   {gu.DM}VAD: {len(speech)} rentang speech terdeteksi{gu.R}")
    else:
        print(f"\r   {gu.YL}!  VAD gagal — pakai fallback trim {FALLBACK_LEAD_TRIM}s{gu.R}")
    all_blocks = snap_to_speech(all_blocks, speech)
    before_sil = len(all_blocks)
    all_blocks = split_on_silence(all_blocks, speech)
    sil_split = len(all_blocks) - before_sil
    all_blocks = fix_timestamps(all_blocks)
    print(
        f"\r   {gu.DM}Post-processing:{gu.R} "
        f"{gu.YL}{dedup_removed} duplikat{gu.R}  "
        f"{gu.RD}{noise_removed} noise{gu.R}  "
        f"{gu.YL}+{split_added} split{gu.R}  "
        f"{gu.YL}+{sil_split} belah sunyi{gu.R}"
    )
    if filtered_out:
        print(f"   {gu.YL}!  {len(filtered_out)} halusinasi dibuang{gu.R}")
    gu.write_srt(all_blocks, output_path)
    elapsed = time.time() - t_start
    speed = duration / elapsed if elapsed else 0
    print(
        f"   {gu.GR}{gu.B}SELESAI{gu.R}  {len(all_blocks)} segmen  "
        f"{fmt_dur(elapsed)}  {gu.B}{speed:.1f}x realtime{gu.R}"
    )
    _jlog({"event":"transcribe_done","video":str(video_path),"segments":len(all_blocks),"lang":lang,"duration":duration,"elapsed":elapsed,"dedup":dedup_removed,"halluc":len(filtered_out),"noise":noise_removed,"split":split_added,"silent":silent_skipped})
    return {"file": video_path.name, "segments": len(all_blocks), "language": lang,
            "duration": duration, "elapsed": elapsed}


# ── ENTRY POINT ──────────────────────────────

def main():
    global GROQ_API_KEYS
    parser = argparse.ArgumentParser(description="Groq Whisper Transcriber \u2014 Multi-Language")
    parser.add_argument("--video", help="Path video tunggal")
    parser.add_argument("--output", help="Path output .srt untuk mode --video")
    parser.add_argument(
        "--lang", default="auto",
        help="Bahasa input video: auto | ja (Jepang) | zh (China) | en (Inggris)  [default: auto]",
    )
    parser.add_argument("--verbose", action="store_true", help="Tampilkan raw error, ffprobe stderr, dan traceback")
    parser.add_argument("--dry-run", action="store_true", help="Preview chunk/durasi tanpa panggil Groq")
    parser.add_argument("--json-log", help="Tulis log JSON terstruktur ke file (append)")
    parser.add_argument("--sync-shift", type=float, default=0.0,
                        help="Kalibrasi sync: geser semua timestamp ±detik (+ = sub muncul lebih lambat) [default: 0.0]")
    parser.add_argument("--snap", help="Snap timing file .srt yang sudah ada ke onset speech, tulis di tempat (tanpa Groq)")
    args = parser.parse_args()
    # ponytail: set globals untuk verbose/json/dry-run/sync-shift biar helper tanpa ubah signature
    global VERBOSE, JSON_LOG, DRY_RUN, SYNC_SHIFT
    VERBOSE = args.verbose
    JSON_LOG = args.json_log
    DRY_RUN = args.dry_run
    SYNC_SHIFT = args.sync_shift or 0.0
    if args.snap:
        # ponytail: mode snap tidak butuh API key — langsung kerja, tanpa banner/rotator
        srt_path = Path(args.snap)
        if not srt_path.exists() or srt_path.suffix.lower() != ".srt":
            print(f"{gu.RD}✘  File SRT tidak ditemukan: {srt_path}{gu.R}")
            sys.exit(1)
        if args.video:
            video = Path(args.video)
            if not video.exists():
                print(f"{gu.RD}✘  Video tidak ditemukan: {video}{gu.R}")
                sys.exit(1)
        else:
            video = guess_video(srt_path)
        if video:
            print(f"   {gu.DM}Video → {video}{gu.R}")
        stats = snap_file(srt_path, video)
        sys.exit(0 if stats else 1)
    if DRY_RUN:
        print(f"   {gu.DM}[dry-run] Preview tanpa panggil Groq — durasi & chunk saja{gu.R}")
    lang = args.lang.strip().lower()
    if lang != "auto" and lang not in LANG_CONFIGS:
        print(f"{gu.RD}\u2718  --lang tidak valid: '{lang}'. Pilih: auto | ja | zh | en{gu.R}")
        sys.exit(1)
    GROQ_API_KEYS = gu.load_groq_api_keys()
    rotator = gu.KeyRotator(GROQ_API_KEYS)
    if not rotator.keys:
        print(f"{gu.RD}\u2718  Tidak ada API key valid. Isi GROQ_API_KEYS di .env project.{gu.R}")
        sys.exit(1)
    banner(rotator, lang)
    if lang == "auto":
        print(f"   {gu.YL}Mode bahasa: auto-detect per video{gu.R}\n")
    if args.video:
        video_path = Path(args.video)
        if not video_path.exists():
            print(f"{gu.RD}\u2718  Video tidak ditemukan: {video_path}{gu.R}")
            sys.exit(1)
        run_lang = lang
        if not args.output and lang == "auto":
            duration = get_video_duration(video_path)
            if not duration:
                print(f"   {gu.RD}\u2718  Tidak bisa baca durasi \u2014 ffprobe error{gu.R}")
                sys.exit(1)
            run_lang = detect_video_language(rotator, video_path, duration)
        out_path = (Path(args.output) if args.output
                    else Path(OUTPUT_FOLDER) / f"{video_path.stem}{LANG_CONFIGS[run_lang]['suffix']}.srt")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        stats = transcribe_video(rotator, video_path, out_path, run_lang)
        if stats:
            out_path.with_suffix(".lang").write_text(stats["language"] + "\n", encoding="utf-8")
        sys.exit(0 if stats else 1)
    # ponytail: folder-batch mode dihapus; pipeline (sub.sh) selalu pakai --video


if __name__ == "__main__":
    main()
