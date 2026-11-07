#!/usr/bin/env python3
"""Read-only SRT quality audit for CatSub output; uses only the Python stdlib."""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

TIMESTAMP_RE = re.compile(
    r"^(\d{2,}:\d{2}:\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{2,}:\d{2}:\d{2})[,.](\d{3})(?:\s+.*)?$"
)
RAW_OUTPUT_RE = re.compile(
    r"\[BELUM_DITERJEMAHKAN\]|```|\b(?:as an ai|here is the translation|"
    r"translation follows|berikut (?:adalah )?terjemahannya|hasil terjemahan:)\b",
    re.IGNORECASE,
)
SOURCE_SCRIPTS = {
    "ja": re.compile(r"[\u3040-\u30ff\u3400-\u9fff]"),
    "zh": re.compile(r"[\u3400-\u9fff]"),
    "ko": re.compile(r"[\uac00-\ud7af]"),
    "th": re.compile(r"[\u0e00-\u0e7f]"),
    "ru": re.compile(r"[\u0400-\u04ff]"),
}


@dataclass
class Cue:
    number: int
    start: int
    end: int
    text: str
    line: int


def timestamp_ms(value: str) -> int:
    hours, minutes, seconds = value.split(":")
    whole, millis = seconds.split(".") if "." in seconds else seconds.split(",")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(whole)) * 1000 + int(millis)


def parse_srt(text: str) -> tuple[list[Cue], list[str]]:
    """Parse cues and report malformed blocks without discarding valid cues."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    errors: list[str] = []
    blocks = re.split(r"\n[ \t]*\n+", text.strip()) if text.strip() else []
    line_offset = 1
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            errors.append(f"baris {line_offset}: blok SRT harus berisi nomor, timestamp, dan teks")
            line_offset += len(lines) + 1
            continue
        try:
            number = int(lines[0].strip())
        except ValueError:
            errors.append(f"baris {line_offset}: nomor cue tidak valid: {lines[0]!r}")
            line_offset += len(lines) + 1
            continue
        match = TIMESTAMP_RE.fullmatch(lines[1].strip())
        if not match:
            errors.append(f"baris {line_offset + 1}: timestamp tidak valid: {lines[1]!r}")
            line_offset += len(lines) + 1
            continue
        start = timestamp_ms(f"{match.group(1)}.{match.group(2)}")
        end = timestamp_ms(f"{match.group(3)}.{match.group(4)}")
        cue_text = "\n".join(lines[2:]).strip()
        if not cue_text:
            errors.append(f"cue {number}: teks kosong")
        cues.append(Cue(number, start, end, cue_text, line_offset))
        line_offset += len(lines) + 1
    return cues, errors


def audit_srt(path: Path, lang: str | None = None,
              source_path: Path | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        cues, parse_errors = parse_srt(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        return [f"tidak bisa membaca file: {exc}"], []
    errors.extend(parse_errors)
    if not cues:
        errors.append("tidak ada cue subtitle yang valid")
        return errors, warnings

    previous_end = -1
    for expected, cue in enumerate(cues, start=1):
        if cue.number != expected:
            warnings.append(f"cue #{cue.number} (baris {cue.line}): nomor tidak berurutan; diharapkan #{expected}")
        if cue.start < 0 or cue.end <= cue.start:
            errors.append(f"cue #{cue.number}: rentang waktu tidak valid")
        if cue.start < previous_end:
            warnings.append(f"cue #{cue.number}: timestamp bertumpang tindih dengan cue sebelumnya")
        previous_end = max(previous_end, cue.end)

        flat = re.sub(r"\s+", " ", cue.text).strip()
        if RAW_OUTPUT_RE.search(flat):
            errors.append(f"cue #{cue.number}: mengandung marker gagal atau teks mentah/instruksi model")
        duration = max((cue.end - cue.start) / 1000, 0.001)
        visible_chars = len(re.sub(r"\s", "", flat))
        cps = visible_chars / duration
        if len(flat) > 84:
            warnings.append(f"cue #{cue.number}: teks panjang ({len(flat)} karakter), pertimbangkan pemenggalan")
        if cps > 24:
            warnings.append(f"cue #{cue.number}: kecepatan baca tinggi ({cps:.1f} karakter/detik)")
        if duration > 7:
            warnings.append(f"cue #{cue.number}: durasi tampil panjang ({duration:.1f} detik)")
        if lang in SOURCE_SCRIPTS:
            letters = [c for c in flat if c.isalpha()]
            source_letters = sum(bool(SOURCE_SCRIPTS[lang].fullmatch(c)) for c in letters)
            if len(letters) >= 4 and source_letters / len(letters) >= 0.25:
                warnings.append(f"cue #{cue.number}: masih banyak aksara bahasa sumber ({lang})")

    if source_path:
        try:
            source_cues, source_errors = parse_srt(source_path.read_text(encoding="utf-8-sig"))
            if source_errors:
                warnings.append(f"SRT sumber memiliki {len(source_errors)} masalah format; perbandingan teks mungkin tidak lengkap")
            source_by_time = {(cue.start, cue.end): cue for cue in source_cues}
            for cue in cues:
                src = source_by_time.get((cue.start, cue.end))
                if src and re.sub(r"\s+", " ", cue.text).strip().casefold() == re.sub(r"\s+", " ", src.text).strip().casefold():
                    # Proper names and short expressions can remain unchanged; report, don't reject.
                    if len(cue.text.strip()) > 12:
                        warnings.append(f"cue #{cue.number}: teks sama persis dengan sumber; periksa apakah belum diterjemahkan")
        except (OSError, UnicodeError) as exc:
            warnings.append(f"SRT sumber tidak bisa dibaca: {exc}")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit struktur dan risiko kualitas file SRT CatSub")
    parser.add_argument("srt", type=Path, help="File SRT yang akan diperiksa")
    parser.add_argument("--lang", choices=sorted(SOURCE_SCRIPTS), help="Bahasa sumber untuk mendeteksi aksara yang tersisa")
    parser.add_argument("--source", type=Path, help="SRT bahasa sumber untuk menandai baris yang identik")
    parser.add_argument("--strict", action="store_true", help="Keluar dengan status gagal jika ada peringatan")
    args = parser.parse_args()

    errors, warnings = audit_srt(args.srt, args.lang, args.source)
    for message in errors:
        print(f"ERROR: {message}")
    for message in warnings:
        print(f"WARN:  {message}")
    if errors:
        print(f"GAGAL: {len(errors)} error, {len(warnings)} peringatan")
        return 1
    if args.strict and warnings:
        print(f"GAGAL (strict): {len(warnings)} peringatan perlu ditinjau")
        return 1
    print(f"OK: struktur SRT valid; {len(warnings)} peringatan untuk ditinjau")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
