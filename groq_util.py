"""
Shared utilities for Groq-based subtitle tools.
"""

import os
import re
import sys
import time
from pathlib import Path
from groq import Groq

# ── ANSI Colors ──────────────────────────────
R = "\033[0m"
B = "\033[1m"
CY = "\033[96m"
GR = "\033[92m"
YL = "\033[93m"
RD = "\033[91m"
DM = "\033[2m"

# ── Shared Regex ─────────────────────────────
LATIN_FRAG_RE = re.compile(r'^[a-zA-Z\u0430-\u044f\u0410-\u042f\u0451\u0401\s.,!?\-\u2013\u2014]+$')
MIXED_JUNK_RE = re.compile(r'[a-zA-Zа-яА-ЯёЁ]{4,}')
JP_CHAR_RE = re.compile(r'[぀-ヿ㐀-鿟＀-￯]')

# ── Hallucination data (shared across srt & whisper) ──
HALLUC_JA_EXACT = {
    "おやすみなさい", "おやすみ",
    "お疲れ様でした", "お疲れ様",
    "ご視聴ありがとうございました", "ご視聴ありがとうございます",
    "ありがとうございました", "ありがとうございます",
    "以上です", "以上", "終わり", "終",
    "ご視聴ありがとうございました。おやすみなさい。",
    "ありがとうございました。おやすみなさい。",
    "ご視聴ありがとうございました。",
}
HALLUC_JA_CONTAINS = {"ご視聴ありがとう", "おやすみなさい", "お疲れ様でした"}

HALLUC_ZH_EXACT = {
    "感谢观看", "谢谢观看", "感谢收看", "谢谢收看",
    "请订阅", "按下订阅", "感谢支持",
    "晚安", "再见", "拜拜", "完", "结束",
}
HALLUC_ZH_CONTAINS = {"感谢观看", "谢谢观看", "感谢收看"}

HALLUC_EN_EXACT = {
    "Thank you for watching.", "Thanks for watching.",
    "Please subscribe.", "Don't forget to subscribe.",
    "Good night.", "Goodbye.", "Bye.", "The end.",
    "Subtitles by", "Translated by",
}
HALLUC_EN_CONTAINS = {"Thank you for watching", "Thanks for watching", "Please subscribe", "Subtitles by"}

# ponytail: desah/napas lintas bahasa — buang kalau segment UTUH berisi ini
BREATH_EXACT = {
    "*sigh*", "*breath*", "*inhale*", "*exhale*", "(breath)", "(sigh)",
    "sigh", "breath", "pant", "exhale", "inhale",
    "ハァ", "ハア", "ふぅ", "ほぅ", "ふふぅ", "呼",        # JP desah
    "嗯", "呼",                                            # ZH desah
}


# ── Key utilities ────────────────────────────

def valid_key(k: str) -> bool:
    if not k:
        return False
    k = k.strip()
    if k in {"gsk_", "XXXXX", "gsk_XXXXX"}:
        return False
    if "XXXXX" in k or "..." in k:
        return False
    return k.startswith("gsk_")


def _read_dotenv(path: Path) -> dict:
    values = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def _dotenv_candidates() -> list:
    script = Path(sys.argv[0] if sys.argv[0] else __file__).resolve()
    candidates = [
        script.parent.parent / ".env",
        script.parent / ".env",
        Path.cwd() / ".env",
    ]
    seen = set()
    result = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _split_keys(raw: str) -> list:
    return [x.strip() for x in re.split(r"[\s,;]+", raw or "") if x.strip()]


def load_groq_api_keys() -> list:
    values = {}
    for path in _dotenv_candidates():
        values.update(_read_dotenv(path))
    values.update(os.environ)
    keys = []
    keys.extend(_split_keys(values.get("GROQ_API_KEYS", "")))
    keys.extend(_split_keys(values.get("GROQ_API_KEY", "")))
    for i in range(1, 51):
        keys.extend(_split_keys(values.get(f"GROQ_API_KEY_{i}", "")))
    unique = []
    seen = set()
    for key in keys:
        if key not in seen and valid_key(key):
            unique.append(key)
            seen.add(key)
    return unique


# ── Key Rotator ──────────────────────────────

class AllKeysExhausted(Exception):
    pass


class KeyRotator:
    def __init__(self, keys: list):
        self.keys = [k.strip() for k in keys if valid_key(k)]
        self.clients = [Groq(api_key=k) for k in self.keys]
        self.idx = 0
        self.cooldowns = {}

    def current(self):
        return self.clients[self.idx], self.idx

    def next(self):
        start = self.idx
        for _ in range(len(self.keys)):
            self.idx = (self.idx + 1) % len(self.keys)
            if time.time() >= self.cooldowns.get(self.idx, 0):
                print(f"\n   {YL}Pindah ke key #{self.idx+1}...{R}", flush=True)
                return self.clients[self.idx], self.idx
            if self.idx == start:
                break
        earliest = min(self.cooldowns.values(), default=0)
        wait = max(0, earliest - time.time())
        if wait > 0:
            print(f"\n   {YL}Semua key cooldown  tunggu {wait:.0f}s{R}", flush=True)
            time.sleep(wait + 1)
        return self.clients[self.idx], self.idx

    def mark_rate_limited(self, idx: int, wait_sec: int = 65):
        self.cooldowns[idx] = time.time() + wait_sec

    def label(self):
        return f"key #{self.idx + 1}"

    def short_key(self, i: int) -> str:
        k = self.keys[i]
        return f"{k[:8]}...{k[-4:]}" if len(k) > 12 else k

    def __len__(self):
        return len(self.keys)


# ── Helpers ──────────────────────────────────

def fmt_dur(s):
    if s < 60:
        return f"{s:.1f}s"
    return f"{int(s // 60)}m {int(s % 60)}s"


def srt_duration_sec(start_str: str, end_str: str) -> float:
    def to_sec(ts):
        ts = ts.replace(",", ".")
        parts = ts.strip().split(":")
        try:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
        except Exception:
            return 0.0
    return max(0.0, to_sec(end_str) - to_sec(start_str))


def _fmt_ts(ts):
    if isinstance(ts, (int, float)):
        return fmt_time(ts)
    s = str(ts).strip()
    return s if s else "00:00:00,000"


def write_srt(blocks, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for i, b in enumerate(blocks, start=1):
            f.write(f"{i}\n{_fmt_ts(b['start'])} --> {_fmt_ts(b['end'])}\n{b['text'].strip()}\n\n")


def fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
