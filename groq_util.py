"""
Shared utilities for Groq-based subtitle tools.
"""

import os
import re
import sys
import time
import json
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
MIXED_JUNK_RE = re.compile(r'[a-zA-Zа-яА-ЯёЁ]{4,}')

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
HALLUC_JA_CONTAINS = {
    "ご視聴ありがとう", "おやすみなさい", "お疲れ様でした",
    # ponytail: frasa halusinasi terkonfirmasi (adegan musik/desahan); whack-a-mole
    # by design — tambah frasa baru tiap ketemu video lain.
    "お父さんの悲しみ", "会社行っちゃう", "アルファレンス",
    "私は2歳です", "私は彼らを愛しています", "お母さんは生きています",
    "私のお母さんのお家庭", "家族を抱えている", "生きていることを知っています",
    "レインボーチャンネル", "とものうち",
}

HALLUC_ZH_EXACT = {
    "感谢观看", "谢谢观看", "感谢收看", "谢谢收看",
    "请订阅", "按下订阅", "感谢支持",
    "晚安", "再见", "拜拜", "完", "结束",
}
HALLUC_ZH_CONTAINS = {
    "感谢观看", "谢谢观看", "感谢收看",
    # ponytail: whack-a-mole ZH halluc dari sample-china2 (2026-08-31)
    "请不吝点赞", "点赞", "打赏支持", "明镜",
}

HALLUC_EN_EXACT = {
    "Thank you for watching.", "Thanks for watching.",
    "Please subscribe.", "Don't forget to subscribe.",
    "Good night.", "Goodbye.", "Bye.", "The end.",
    "Subtitles by", "Translated by",
    "Please like and subscribe.", "Don't forget to like and subscribe.",
    "Like and subscribe.",
    "I'm a student at the University of Arizona.",
    "I am a student at the University of Arizona.",
    "i a student at the university of arizona",
    "thank you jesus.", "Thank you Jesus.",
}
HALLUC_EN_CONTAINS = {
    "Thank you for watching", "Thanks for watching",
    "Please subscribe", "Subtitles by",
    "thank you for watching", "thanks for watching",
    "please subscribe", "subtitles by",
    "like and subscribe", "Like and subscribe",
    "university of arizona", "University of Arizona",
    "at the university of arizona",
}

HALLUC_KO_EXACT = {
    "시청해 주셔서 감사합니다", "시청해주셔서 감사합니다",
    "구독 부탁드립니다", "구독해주세요", "좋아요 눌러주세요",
    "안녕히 주무세요", "안녕", "끝",
}

HALLUC_KO_CONTAINS = {
    "시청해 주셔서 감사합니다", "구독 부탁드립니다",
    # ponytail: whack-a-mole KO halluc tipikal Whisper (closing/subscribe)
    "시청해 주셔서", "구독",
}

REPEATED_PHRASE_RE = re.compile(r"(.{10,})\s+\1")


def repeated_phrase(text: str) -> str:
    """Deteksi frasa terulang (hallucination Whisper klasik), lintas bahasa."""
    m = REPEATED_PHRASE_RE.search(text.strip())
    return m.group(1).strip() if m else ""


# ponytail: kata sama diulang ≥4x dalam satu baris — split pada separator JP dulu,
# karena \b word boundary tidak bekerja di Japanese (tanpa spasi antar kata).
# Hanya flag kata pendek (≤3 char) ≥4x — threshold konservatif untuk short fillers
# (うん/あ/え) yang hampir pasti Whisper artifact. 3x masih bisa dialogue asli
# ("暑いな、暑いな、暑いな" = character ngomong), jangan auto-remove.
# Fallback: concatenated ("いくいくいくいく") dan char-run garble ("ううううううう").
_JPSEP = re.compile(r"[,。\s、！?？!]+")

def has_filler_repetition(text: str) -> str:
    """Kata pendek (≤3 char) diulang ≥4x. Return kata, atau ''."""
    from collections import Counter
    t = text.strip()
    # 1) Counter — pisah separator: "うん、うん、うん、うん"
    tokens = [w.lower() for w in _JPSEP.split(t) if len(w) >= 2]
    for word, count in Counter(tokens).items():
        if count >= 4 and len(word) <= 3:
            return word
    # 2) Char-run garble: "ううううううううう" (Whisper artifact, single char ≥6x)
    #    Dicek SEBELUM concatenated — karena "ううう" ×3 bisa match concatenated.
    m = re.search(r"([\u3040-\u9fff])\1{5,}", t)
    if m:
        return m.group(1) * 2
    # 3) Concatenated: "いくいくいくいくいくいくいくいく"
    m = re.search(r"(\w{2,3})\1{2,}", t)
    if m:
        return m.group(1)
    return ""


def check_latin_junk(text: str, src_char_re, label: str) -> str:
    """Teks Latin murni tanpa karakter bahasa sumber → junk. Dipakai semua
    bahasa non-English (char_re bahasa tl memang Latin, jadi otomatis lolos)."""
    t = text.strip()
    latin_words = MIXED_JUNK_RE.findall(t)
    src_chars = src_char_re.findall(t)
    if latin_words and not src_chars:
        return f"kata Latin tanpa konteks {label}: {latin_words}"
    return ""


# ponytail: desah/napas lintas bahasa — buang kalau segment UTUH berisi ini
BREATH_EXACT = {
    "*sigh*", "*breath*", "*inhale*", "*exhale*", "(breath)", "(sigh)",
    "sigh", "breath", "pant", "exhale", "inhale",
    "ハァ", "ハア", "ふぅ", "ほぅ", "ふふぅ", "呼",        # JP desah
    "あぁ", "ああ",                                      # JP 2-char desah lolos len>2
    "ぬはぁ", "ぐはぁ", "ぷのはぁ", "きゅきゅ",              # JP desah (terkonfirmasi NOSKN-103)
    "アア",                                                     # JP desah katakana (mirip ああ; terkonfirmasi MIUM-1437)
    "けんふ", "んくるっん",                                     # JP garble non-kata (terkonfirmasi MIUM-1437)
    "嗯", "呼", "哈", "啊", "额", "呃",                      # ZH desah
    "아", "어", "응", "흐", "헉", "하",                        # KO desah
}

# ponytail: segmen UTUH yang seluruh karakternya dari kelas desah/napas JA+ZH+KO
# (varian campuran seperti あぁはぁっ / ハァハァ / んぅあぁ / 아아하아) — lebih luas dari
# BREATH_EXACT tanpa daftar per-frasa. Kata nyata hampir selalu mengandung char
# di luar kelas (さ/し/た/て/と/な/の...), jadi risiko buang ucapan asli rendah.
# ponytail: ZH tambah 哈额呃, KO tambah 아어응흐헉하 (whack-a-mole umum)
BREATH_CHARS_RE = re.compile(
    "^[" 
    "あぁぃいぅうぇえぉおっゃゅょゎわんぬぐはばぱひふへぼぽ"
    "アィイゥウェェォオッャュョヮワンヴァハバパヒフヘボポ"
    "嗯唔哦噢喔啊呀呣哼哈额呃"
    "아어응흐헉하아어"
    "]+$"
)


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
        self.clients = [Groq(api_key=k, timeout=600) for k in self.keys]  # ponytail: tanpa ini request bisa hang selamanya
        self.idx = 0
        self.cooldowns = {}
        # ponytail: batas tidur next() — None = warisan (transcribe, tidur
        # berapa pun); srt.py set 300 detik biar cooldown jam-an (marker daily)
        # jadi gagal-cepat, bukan hang diam-diam
        self.max_wait = None

    def current(self):
        return self.clients[self.idx], self.idx

    def next(self, quiet=False):
        # ponytail: quiet=True untuk rotasi proaktif terjadwal (pacing TPM) —
        # tanpa cetak 'Pindah...' supaya tak dikira kena limit; rotasi karena
        # error tetap berisik (ada baris '! Rate/Daily limit' sebelumnya)
        start = self.idx
        for _ in range(len(self.keys)):
            self.idx = (self.idx + 1) % len(self.keys)
            if time.time() >= self.cooldowns.get(self.idx, 0):
                if not quiet:
                    print(f"\n   {YL}Pindah ke key #{self.idx+1}...{R}", flush=True)
                return self.clients[self.idx], self.idx
            if self.idx == start:
                break
        earliest = min(self.cooldowns.values(), default=0)
        wait = max(0, earliest - time.time())
        if wait > 0:
            if self.max_wait is not None and wait > self.max_wait:
                raise AllKeysExhausted()
            print(f"\n   {YL}Semua key cooldown  tunggu {wait:.0f}s{R}", flush=True)
            time.sleep(wait + 1)
        return self.clients[self.idx], self.idx

    def mark_rate_limited(self, idx: int, wait_sec: int = 65):
        self.cooldowns[idx] = time.time() + wait_sec

    def mark_invalid(self, idx: int):
        """Tandai key invalid permanen (401) — set cooldown sangat lama."""
        if 0 <= idx < len(self.keys):
            self.cooldowns[idx] = time.time() + 31536000  # 1 tahun

    def label(self):
        return f"key #{self.idx + 1}"

    def note_usage(self, idx: int, model: str, prompt_t: int, compl_t: int):
        """Catat token terpakai ke ledger harian (auxiliary — gagal tulis ≠ gagal job)."""
        try:
            add_usage(idx, model, prompt_t, compl_t)
        except Exception:
            pass

    def short_key(self, i: int) -> str:
        k = self.keys[i]
        return f"{k[:8]}...{k[-4:]}" if len(k) > 12 else k

    def __len__(self):
        return len(self.keys)


# ── Ledger token harian ──────────────────────
# ponytail: Groq tidak kasih header sisa TPD harian (cuma sisa TPM/RPD) —
# sisa kuota wajib dicatat lokal dari usage tiap response. Key = tanggal lokal
# (reset otomatis tiap hari) → key_idx → model → [prompt, completion].
USAGE_FILE = Path(__file__).resolve().parent / "srt" / ".usage.json"


def _today():
    return time.strftime("%Y-%m-%d")


def _load_ledger(path=USAGE_FILE):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data.get(_today(), {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def add_usage(idx: int, model: str, prompt_t: int, compl_t: int,
              path=USAGE_FILE):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    day = data.setdefault(_today(), {})
    entry = day.setdefault(str(idx), {})
    p, c = entry.get(model, [0, 0])
    entry[model] = [p + max(0, int(prompt_t)), c + max(0, int(compl_t))]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def ledger_used(idx: int, model: str, path=USAGE_FILE) -> int:
    entry = _load_ledger(path).get(str(idx), {})
    p, c = entry.get(model, [0, 0])
    return p + c


def set_usage(idx: int, model: str, prompt_t: int, compl_t: int,
              path=USAGE_FILE):
    """Timpa (bukan tambah) catatan key — untuk marker exhausted."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    day = data.setdefault(_today(), {})
    entry = day.setdefault(str(idx), {})
    entry[model] = [max(0, int(prompt_t)), max(0, int(compl_t))]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def mark_exhausted(idx: int, model: str, limit: int, path=USAGE_FILE):
    """Tandai org/key daily-habis (persisten antar-proses, ber-stempel waktu).

    ponytail: cooldown in-memory hilang saat proses mati → tanpa ini setiap run
    baru mengulang siklus 429 sia-sia di key yang sudah mati harian. Marker di
    luar bucket tanggal: window Groq ROLLING (bukan reset tengah malam —
    terbukti 2026-09-29: 30k pasca-midnight tetap 429), jadi expiry = hit +
    window, bukan ganti hari."""
    try:
        total = max(limit, ledger_used(idx, model, path))
    except Exception:
        total = limit
    set_usage(idx, model, total, 0, path=path)
    try:
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        marks = data.setdefault("_marks", {})
        marks[str(idx)] = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def daily_blocked(idx: int, skip_sec: float, path=USAGE_FILE,
                  now=0.0) -> float:
    """Detik tersisa key diblokir (0 = bebas). Kadaluarsa otomatis."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        ts = (data.get("_marks", {}) or {}).get(str(idx), 0)
        return max(0.0, float(ts) + skip_sec - (now if now else time.time()))
    except Exception:
        return 0.0


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
