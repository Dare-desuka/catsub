#!/usr/bin/env python3
"""Transcribe a video to source-language SRT using Groq Whisper."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import groq_util as gu

MODEL = "whisper-large-v3"
CHUNK_MINUTES = 10
OVERLAP_SEC = 3
LANGUAGES = {
    "ja": ("ja", "日本語", "_JA"),
    "zh": ("zh", "中文", "_ZH"),
    "en": ("en", "English", "_EN"),
    "ko": ("ko", "한국어", "_KO"),
    "th": ("th", "ไทย", "_TH"),
    "tl": ("tl", "Filipino", "_TL"),
    "ru": ("ru", "Русский", "_RU"),
}
LANG_ALIASES = {
    "japanese": "ja", "japan": "ja", "日本語": "ja",
    "chinese": "zh", "mandarin": "zh", "cmn": "zh", "中文": "zh",
    "english": "en", "eng": "en",
    "korean": "ko", "한국어": "ko",
    "thai": "th", "thailand": "th", "ไทย": "th",
    "fil": "tl", "filipino": "tl", "tagalog": "tl",
    "rus": "ru", "russian": "ru", "русский": "ru",
}


def get_video_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe gagal membaca video: {result.stderr.strip()}")
    try:
        duration = float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Durasi video tidak tersedia atau tidak valid") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f"Durasi video tidak valid: {duration}")
    return duration


def extract_audio_chunk(video: Path, start: float, duration: float, output: Path) -> None:
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(start),
         "-i", str(video), "-t", str(duration), "-vn", "-af",
         "highpass=f=80,afftdn=nf=-20", "-acodec", "libmp3lame",
         "-ar", "16000", "-ac", "1", "-q:a", "3", str(output)],
        capture_output=True, text=True, timeout=300,
    )
    if result.returncode or not output.is_file() or output.stat().st_size == 0:
        detail = result.stderr.strip() or "file audio tidak dibuat"
        raise RuntimeError(f"Ekstraksi audio gagal pada {start:.1f}s: {detail}")


def infer_language(text: str, reported: str = "") -> str | None:
    reported = (reported or "").strip().lower()
    if reported in LANGUAGES:
        return reported
    if reported in LANG_ALIASES:
        return LANG_ALIASES[reported]
    # Script-based fallback for ambiguous/unknown API language labels.
    if any("\u3040" <= c <= "\u30ff" for c in text):
        return "ja"
    if any("\uac00" <= c <= "\ud7a3" for c in text):
        return "ko"
    if any("\u0e00" <= c <= "\u0e7f" for c in text):
        return "th"
    if any("\u0400" <= c <= "\u04ff" for c in text):
        return "ru"
    if any("\u4e00" <= c <= "\u9fff" for c in text):
        return "zh"
    if any(c.isascii() and c.isalpha() for c in text):
        return "en"
    return None


def _response_value(segment, key: str, default=None):
    return segment.get(key, default) if isinstance(segment, dict) else getattr(segment, key, default)


def transcribe_audio(rotator: gu.KeyRotator, audio: Path, offset: float, lang: str) -> list[dict]:
    client, key_idx = rotator.current()
    for attempt in range(1, 4):
        try:
            with audio.open("rb") as stream:
                response = client.audio.transcriptions.create(
                    file=(audio.name, stream, "audio/mpeg"), model=MODEL,
                    language=LANGUAGES[lang][0], response_format="verbose_json",
                    timestamp_granularities=["segment"], temperature=0.0,
                )
            result = []
            for segment in response.segments or []:
                text = str(_response_value(segment, "text", "")).strip()
                if not text:
                    continue
                start = float(_response_value(segment, "start", 0)) + offset
                end = float(_response_value(segment, "end", start - offset)) + offset
                if end <= start:
                    continue
                result.append({"start": round(start, 3), "end": round(end, 3), "text": text})
            rotator.next()
            return result
        except Exception as exc:
            message = str(exc).lower()
            if "429" in message or "rate" in message:
                rotator.mark_rate_limited(key_idx)
                client, key_idx = rotator.next()
            elif attempt < 3:
                time.sleep(2 * attempt)
            else:
                raise RuntimeError(f"Whisper API gagal setelah {attempt} percobaan: {exc}") from exc
    raise RuntimeError("Transkripsi gagal setelah percobaan ulang")


def detect_video_language(rotator: gu.KeyRotator, video: Path, duration: float) -> str:
    sample_duration = min(60.0, duration)
    starts = [0.0]
    if duration > 180:
        starts.extend([duration * 0.25, duration * 0.5])
    votes: Counter[str] = Counter()
    with tempfile.TemporaryDirectory(prefix="catsub-detect-") as temp:
        for number, start in enumerate(starts, 1):
            start = min(start, max(0.0, duration - sample_duration))
            audio = Path(temp) / f"detect-{number}.mp3"
            extract_audio_chunk(video, start, sample_duration, audio)
            client, key_idx = rotator.current()
            detected = None
            for attempt in range(1, 4):
                try:
                    with audio.open("rb") as stream:
                        response = client.audio.transcriptions.create(
                            file=(audio.name, stream, "audio/mpeg"), model=MODEL,
                            response_format="verbose_json", temperature=0.0,
                        )
                    segments = response.segments or []
                    text = " ".join(str(_response_value(s, "text", "")).strip() for s in segments)
                    detected = infer_language(text, getattr(response, "language", ""))
                    rotator.next()
                    break
                except Exception as exc:
                    message = str(exc).lower()
                    if "429" in message or "rate" in message:
                        rotator.mark_rate_limited(key_idx)
                        client, key_idx = rotator.next()
                    elif attempt < 3:
                        time.sleep(2 * attempt)
                    else:
                        raise RuntimeError(f"Deteksi bahasa gagal: {exc}") from exc
            if detected:
                votes[detected] += 1
    if not votes:
        raise RuntimeError(
            "Bahasa sumber tidak dapat dideteksi dengan yakin. Jalankan ulang dengan "
            "--lang ja|zh|en|ko|th|tl|ru; tidak ada fallback otomatis ke Jepang."
        )
    # A tie is ambiguous; ask the caller to choose instead of silently guessing.
    top = votes.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        raise RuntimeError(
            "Deteksi bahasa ambigu (" + ", ".join(f"{lang}:{count}" for lang, count in top)
            + "). Tentukan bahasa secara manual dengan --lang."
        )
    return top[0][0]


def deduplicate_overlap(blocks: list[dict]) -> list[dict]:
    """Remove only near-identical adjacent text caused by chunk overlap."""
    result: list[dict] = []
    for block in blocks:
        if result:
            previous = result[-1]
            normalize = lambda value: "".join(value.lower().split()).strip(".,!?。、「」")
            gap = block["start"] - previous["end"]
            if normalize(block["text"]) == normalize(previous["text"]) and gap < 6:
                # Preserve the longer timing interval, not a potentially valid utterance
                # merely because it is short or sounds like a filler.
                previous["start"] = min(previous["start"], block["start"])
                previous["end"] = max(previous["end"], block["end"])
                continue
        result.append(block)
    return result


def transcribe_video(rotator: gu.KeyRotator, video: Path, output: Path, lang: str) -> dict:
    duration = get_video_duration(video)
    if lang == "auto":
        lang = detect_video_language(rotator, video, duration)
    if lang not in LANGUAGES:
        raise ValueError(f"Bahasa tidak didukung: {lang}")

    chunk_seconds = CHUNK_MINUTES * 60
    stride = chunk_seconds - OVERLAP_SEC
    chunk_count = math.ceil(max(0, duration - OVERLAP_SEC) / stride)
    blocks: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="catsub-audio-") as temp:
        for index in range(chunk_count):
            start = index * stride
            length = min(chunk_seconds, duration - start)
            if length <= 0:
                break
            audio = Path(temp) / f"chunk-{index:04d}.mp3"
            print(f"Transcribe chunk {index + 1}/{chunk_count} ({start:.1f}s–{start + length:.1f}s)", flush=True)
            # A failed chunk invalidates the transcript. Do not silently return a
            # plausible-looking SRT with a missing section of the video.
            extract_audio_chunk(video, start, length, audio)
            blocks.extend(transcribe_audio(rotator, audio, start, lang))
            if index < chunk_count - 1:
                time.sleep(1)

    blocks.sort(key=lambda item: (item["start"], item["end"]))
    blocks = deduplicate_overlap(blocks)
    if not blocks:
        raise RuntimeError("Whisper tidak menghasilkan segmen; output tidak ditandai berhasil.")

    output.parent.mkdir(parents=True, exist_ok=True)
    # Write atomically: a crash should not leave a truncated cache that looks complete.
    temporary_output = output.with_name(output.name + ".tmp")
    gu.write_srt(blocks, temporary_output)
    temporary_output.replace(output)
    return {"file": video.name, "segments": len(blocks), "language": lang, "duration": duration}


def main() -> int:
    parser = argparse.ArgumentParser(description="Groq Whisper transcription to SRT")
    parser.add_argument("--video", required=True, help="Path video")
    parser.add_argument("--output", required=True, help="Path output .srt")
    parser.add_argument("--lang", default="auto", help="auto | ja | zh | en | ko | th | tl | ru")
    args = parser.parse_args()
    lang = args.lang.strip().lower()
    if lang != "auto" and lang not in LANGUAGES:
        parser.error("--lang harus auto, ja, zh, en, ko, th, tl, atau ru")
    video = Path(args.video).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not video.is_file():
        parser.error(f"Video tidak ditemukan: {video}")
    keys = gu.load_groq_api_keys()
    if not keys:
        print("Tidak ada GROQ_API_KEYS valid di .env atau environment.", file=sys.stderr)
        return 1
    rotator = gu.KeyRotator(keys)
    try:
        stats = transcribe_video(rotator, video, output, lang)
    except Exception as exc:
        print(f"Transkripsi gagal: {exc}", file=sys.stderr)
        return 1
    label = LANGUAGES[stats["language"]][1]
    output.with_suffix(".lang").write_text(stats["language"] + "\n", encoding="utf-8")
    print(f"Selesai: {stats['segments']} segmen, bahasa {label}, output {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
