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
from pathlib import Path

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
    },
    "en": {
        "label": "English",
        "whisper_lang": "en",
        "suffix": "_EN",
        "fillers": {
            "um","uh","uhh","hmm","hm","mm","mmm",
            "ah","ahh","oh","ohh","eh","ehh",
            "like","you know","i mean",
        },
        "halluc_exact": gu.HALLUC_EN_EXACT,
        "halluc_contains": gu.HALLUC_EN_CONTAINS,
        "char_re": re.compile(r'[a-zA-Z]'),
    },
}

LANG_ALIASES = {
    "ja": "ja", "japanese": "ja", "japan": "ja", "\u65e5\u672c\u8a9e": "ja",
    "zh": "zh", "chinese": "zh", "mandarin": "zh", "\u4e2d\u6587": "zh", "cmn": "zh",
    "en": "en", "eng": "en", "english": "en",
}


# ── BANNER & UTILS ──────────────────────────

def banner(rotator: gu.KeyRotator, lang: str):
    cfg = LANG_CONFIGS.get(lang)
    flag = cfg["label"][:1] if cfg else "?"
    label = cfg["label"] if cfg else "auto-detect"
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
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video_path)],
            capture_output=True, text=True, timeout=15,
        )
        info = json.loads(probe.stdout)
        return float(info["format"]["duration"])
    except Exception:
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
    if len(clean) <= 2:
        return True
    char_count = Counter(clean)
    if char_count.most_common(1)[0][1] / len(clean) >= 0.55:
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
    if len(t) <= 2 and not cfg["char_re"].search(t):
        return True, f"terlalu pendek tanpa karakter {lang}: {t!r}"
    if lang == "ja":
        if gu.LATIN_FRAG_RE.match(t) and len(t) > 6:
            return True, f"murni Latin/Cyrillic: {t!r}"
        latin_words = gu.MIXED_JUNK_RE.findall(t)
        jp_chars = cfg["char_re"].findall(t)
        if latin_words and not jp_chars:
            return True, f"kata Latin tanpa konteks JP: {latin_words}"
    if duration_sec >= 20.0 and len(t) <= 25:
        return True, f"teks pendek ({len(t)} char) di segment panjang ({duration_sec:.0f}s)"
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
        split_re = re.compile(r'(?<=[\u3002\uff0c])\s*')
    elif lang == "zh":
        split_re = re.compile(r'(?<=[\u3002\uff0c\uff0c])\s*')
    else:
        split_re = re.compile(r'(?<=[.!?])\s+')
    result = []
    for b in blocks:
        text = b["text"].strip()
        duration = b["end"] - b["start"]
        words = b.get("words", [])
        sentences = [s.strip() for s in split_re.split(text) if s.strip()]
        if len(sentences) <= 1 or len(text) <= max_chars:
            result.append(b)
            continue
        if not words or len(words) < len(sentences):
            result.append(b)
            continue
        if duration < 0.8 * len(sentences):
            result.append(b)
            continue
        n_words = len(words)
        total_chars = sum(len(re.sub(r'\s+', '', s)) for s in sentences)
        cursor_word = 0
        for si, sent in enumerate(sentences):
            is_last = (si == len(sentences) - 1)
            sent_len = len(re.sub(r'\s+', '', sent))
            ratio = sent_len / total_chars if total_chars else 1 / len(sentences)
            n_words_est = max(1, round(n_words * ratio))
            if is_last:
                sent_words = words[cursor_word:]
            else:
                end_idx = min(n_words, cursor_word + n_words_est)
                sent_words = words[cursor_word:end_idx]
                cursor_word = end_idx
                if cursor_word >= n_words:
                    cursor_word = n_words - 1
            if sent_words:
                start_ts = sent_words[0]["start"]
                end_ts = sent_words[-1]["end"]
            else:
                start_ts = b["start"]
                end_ts = b["end"]
            result.append({"start": round(start_ts, 3), "end": round(end_ts, 3),
                          "text": sent, "words": sent_words})
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
                )
            segments = response.segments or []
            blocks = []
            for seg in segments:
                text = (seg["text"] if isinstance(seg, dict) else seg.text).strip()
                start = (seg["start"] if isinstance(seg, dict) else seg.start)
                end = (seg["end"] if isinstance(seg, dict) else seg.end)
                if not text:
                    continue
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
                              "text": text, "words": words})
            rotator.next()
            return blocks
        except Exception as e:
            err_str = str(e).lower()
            if "rate" in err_str or "429" in err_str:
                print(f"\n   {gu.YL}Key #{key_idx+1} rate-limit \u2014 pindah key{gu.R}", flush=True)
                rotator.mark_rate_limited(key_idx)
                client, key_idx = rotator.next()
                time.sleep(2)
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
            if "rate" in err_str or "429" in err_str:
                print(f"\n   {gu.YL}Key #{key_idx+1} rate-limit saat deteksi \u2014 pindah key{gu.R}", flush=True)
                rotator.mark_rate_limited(key_idx)
                client, key_idx = rotator.next()
                time.sleep(2)
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
    with tempfile.TemporaryDirectory() as tmpdir:
        for idx, start_sec in enumerate(starts, start=1):
            start_sec = min(start_sec, max(0.0, duration - sample_duration))
            audio_path = Path(tmpdir) / f"detect_{idx:02d}.mp3"
            if not extract_audio_chunk(video_path, start_sec, sample_duration, audio_path):
                continue
            lang, text, reported = detect_language_from_audio(rotator, audio_path)
            audio_path.unlink(missing_ok=True)
            if lang in LANG_CONFIGS:
                cfg = LANG_CONFIGS[lang]
                report = f", Whisper: {reported}" if reported else ""
                print(f"   {gu.GR}\u2714  Bahasa terdeteksi: {cfg['label']}{gu.DM}{report}{gu.R}")
                return lang
            if text:
                print(f"   {gu.YL}!  Sampel {idx} belum jelas, coba sampel lain\u2026{gu.R}")
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
        return {}
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
    all_blocks = []
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
            blocks = transcribe_chunk(rotator, audio_path, start_sec, lang)
            all_blocks.extend(blocks)
            audio_path.unlink(missing_ok=True)
            if ci < n_chunks - 1:
                time.sleep(1.0)
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
    all_blocks = fix_timestamps(all_blocks)
    print(
        f"\r   {gu.DM}Post-processing:{gu.R} "
        f"{gu.YL}{dedup_removed} duplikat{gu.R}  "
        f"{gu.RD}{noise_removed} noise{gu.R}  "
        f"{gu.YL}+{split_added} split{gu.R}"
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
    args = parser.parse_args()
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
