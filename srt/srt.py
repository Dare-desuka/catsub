#!/usr/bin/env python3
"""
Groq Subtitle Translator (GPT-OSS 120B) — Multi-Language Input → Indonesia
Input  : SRT dari Jepang (_JA) / China (_ZH) / Inggris (_EN)
Output : SRT Indonesia (_ID)
"""
import os
import re
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import groq_util as gu

# ─────────────────────────────────────────────
#  KONFIGURASI
# ─────────────────────────────────────────────

GROQ_API_KEYS = []
MODEL = "openai/gpt-oss-120b"
BASE_DIR = Path(__file__).resolve().parent
input_folder = str(BASE_DIR / "queue")
output_folder = str(BASE_DIR / "subtitles")
BATCH_SIZE = 8
REQUEST_DELAY = 3
MAX_RETRY_BAD_FORMAT = 4
MARK_FAILED_IN_OUTPUT = True

# ─────────────────────────────────────────────
#  KONFIGURASI BAHASA SUMBER → INDONESIA
# ─────────────────────────────────────────────

SRC_LANG_CONFIGS = {
    "ja": {
        "label": "Jepang",
        "suffix_in": "_JA",
        "suffix_out": "_ID",
        "check_src_chars": True,
        "src_char_re": re.compile(r"[\u3040-\u30ff\u3400-\u9fff]"),
        "halluc_exact": gu.HALLUC_JA_EXACT,
        "halluc_contains": gu.HALLUC_JA_CONTAINS,
        "system": """Kamu adalah penerjemah subtitle profesional dari Bahasa Jepang ke Bahasa Indonesia.

Aturan WAJIB:
1. Terjemahkan subtitle Jepang ke Bahasa Indonesia yang natural dan enak dibaca.
2. Pertahankan makna, konteks, nada bicara, dan emosi.
3. Jangan membuat ringkasan.
4. Jangan menambah informasi baru.
5. Jangan menghapus baris.
6. Jangan menggabungkan dua ID menjadi satu.
7. Jangan memecah satu ID menjadi beberapa ID.
8. Output HARUS memakai format yang sama:
   [001] terjemahan
   [002] terjemahan
9. Semua ID input HARUS muncul di output.
10. Jangan tulis penjelasan, catatan, markdown, atau komentar.
11. Jika input berupa ekspresi pendek seperti あ, え, いや, うん, terjemahkan singkat:
    "Ah", "Eh", "Hah", "Iya", "Nggak", "...", atau "-" sesuai konteks.
12. Jangan pernah mengosongkan terjemahan setelah ID.
13. Erangan, desahan, dan onomatopoeia (mis. ああ, うん, いくいく, メルメル, チー) terjemahkan
    ke padanan Indonesia singkat ("Ah", "Hmm", "...", "-") atau buang — JANGAN tulis ulang
    dalam romaji (mis. jangan jadi "Ikkui", "Merumeru", "Aik").
14. Yang dipertahankan apa adanya hanya nama orang/tempat/merek/kode asli; onomatope dan erangan
    BUKAN merek, jadi tetap harus diterjemahkan/dibuang.
15. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
16. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.

Glossary wajib (konsisten, jangan ganti-ganti):
- オシュレット / オシュレト → bidet (semprot toilet)
- チンポ / チンコ / ちんちん / おちんちん / ペニス / チン → penis
- Varian yang sering salah dengar Whisper juga penis: シンポ / キンコ / きんじん / イチンポ → penis
- クリ / くり → klitoris
- マンコ / まんこ / おまんこ → memek
- ペニマンコ → penis dan memek
- Kata vulgar diterjemahkan dengan kata vulgar yang natural, JANGAN dibikin
  klinis ("alat kelamin") atau diterjemahkan ngawur ("wastafel", dst).
""",
        "user_header": lambda n, ids: (
            f"Terjemahkan subtitle berikut dari Bahasa Jepang ke Bahasa Indonesia.\n\n"
            f"Jumlah item: {n}\nID yang wajib ada: {ids}"
        ),
        "user_rules": (
            "- Output hanya terjemahan.\n"
            "- Format wajib: [ID] terjemahan\n"
            "- Semua ID wajib muncul.\n"
            "- Urutan ID wajib sama.\n"
            "- Jangan gabungkan ID.\n"
            "- Jangan hapus ID.\n"
            "- Jangan kosongkan isi terjemahan.\n"
        ),
    },
    "zh": {
        "label": "China",
        "suffix_in": "_ZH",
        "suffix_out": "_ID",
        "check_src_chars": False,
        "src_char_re": re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]"),
        "halluc_exact": gu.HALLUC_ZH_EXACT,
        "halluc_contains": gu.HALLUC_ZH_CONTAINS,
        "system": """Kamu adalah penerjemah subtitle profesional dari Bahasa Mandarin (中文) ke Bahasa Indonesia.

Aturan WAJIB:
1. Terjemahkan subtitle Mandarin ke Bahasa Indonesia yang natural dan enak dibaca.
2. Pertahankan makna, konteks, nada bicara, dan emosi.
3. Jangan membuat ringkasan.
4. Jangan menambah informasi baru.
5. Jangan menghapus baris.
6. Jangan menggabungkan dua ID menjadi satu.
7. Jangan memecah satu ID menjadi beberapa ID.
8. Output HARUS memakai format yang sama:
   [001] terjemahan
   [002] terjemahan
9. Semua ID input HARUS muncul di output.
10. Jangan tulis penjelasan, catatan, markdown, atau komentar.
11. Jika input berupa ekspresi pendek seperti 嗯, 啊, 哦, terjemahkan singkat:
    "Hmm", "Ah", "Oh", "...", atau "-" sesuai konteks.
12. Jangan pernah mengosongkan terjemahan setelah ID.
13. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
14. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.
""",
        "user_header": lambda n, ids: (
            f"Terjemahkan subtitle berikut dari Bahasa Mandarin ke Bahasa Indonesia.\n\n"
            f"Jumlah item: {n}\nID yang wajib ada: {ids}"
        ),
        "user_rules": (
            "- Output hanya terjemahan.\n"
            "- Format wajib: [ID] terjemahan\n"
            "- Semua ID wajib muncul.\n"
            "- Urutan ID wajib sama.\n"
            "- Jangan gabungkan ID.\n"
            "- Jangan hapus ID.\n"
            "- Jangan kosongkan isi terjemahan.\n"
        ),
    },
    "en": {
        "label": "Inggris",
        "suffix_in": "_EN",
        "suffix_out": "_ID",
        "check_src_chars": False,
        "src_char_re": re.compile(r"[a-zA-Z]"),
        "halluc_exact": gu.HALLUC_EN_EXACT,
        "halluc_contains": gu.HALLUC_EN_CONTAINS,
        "system": """Kamu adalah penerjemah subtitle profesional dari Bahasa Inggris ke Bahasa Indonesia.

Aturan WAJIB:
1. Terjemahkan subtitle Inggris ke Bahasa Indonesia yang natural dan enak dibaca.
2. Pertahankan makna, konteks, nada bicara, dan emosi.
3. Jangan membuat ringkasan.
4. Jangan menambah informasi baru.
5. Jangan menghapus baris.
6. Jangan menggabungkan dua ID menjadi satu.
7. Jangan memecah satu ID menjadi beberapa ID.
8. Output HARUS memakai format yang sama:
   [001] terjemahan
   [002] terjemahan
9. Semua ID input HARUS muncul di output.
10. Jangan tulis penjelasan, catatan, markdown, atau komentar.
11. Jika input berupa ekspresi pendek seperti "Ah", "Oh", "Hmm", terjemahkan natural:
    "Ah", "Oh", "Hmm", "...", atau "-" sesuai konteks.
12. Jangan pernah mengosongkan terjemahan setelah ID.
13. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
14. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.
""",
        "user_header": lambda n, ids: (
            f"Terjemahkan subtitle berikut dari Bahasa Inggris ke Bahasa Indonesia.\n\n"
            f"Jumlah item: {n}\nID yang wajib ada: {ids}"
        ),
        "user_rules": (
            "- Output hanya terjemahan.\n"
            "- Format wajib: [ID] terjemahan\n"
            "- Semua ID wajib muncul.\n"
            "- Urutan ID wajib sama.\n"
            "- Jangan gabungkan ID.\n"
            "- Jangan hapus ID.\n"
            "- Jangan kosongkan isi terjemahan.\n"
        ),
    },
    "ko": {
        "label": "Korea",
        "suffix_in": "_KO", "suffix_out": "_ID",
        "check_src_chars": False,
        "src_char_re": re.compile(r"[\uac00-\ud7a3]"),
        "halluc_exact": set(),  # ponytail: kosong dulu
        "halluc_contains": set(),
        "system": """Kamu adalah penerjemah subtitle profesional dari Bahasa Korea ke Bahasa Indonesia.

Aturan WAJIB:
1. Terjemahkan subtitle Korea ke Bahasa Indonesia yang natural dan enak dibaca.
2. Pertahankan makna, konteks, nada bicara, dan emosi.
3. Jangan membuat ringkasan.
4. Jangan menambah informasi baru.
5. Jangan menghapus baris.
6. Jangan menggabungkan dua ID menjadi satu.
7. Jangan memecah satu ID menjadi beberapa ID.
8. Output HARUS memakai format yang sama:
   [001] terjemahan
   [002] terjemahan
9. Semua ID input HARUS muncul di output.
10. Jangan tulis penjelasan, catatan, markdown, atau komentar.
11. Jika input berupa ekspresi pendek, terjemahkan singkat dan natural sesuai konteks.
12. Jangan pernah mengosongkan terjemahan setelah ID.
13. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
14. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.
""",
        "user_header": lambda n, ids: (
            f"Terjemahkan subtitle berikut dari Bahasa Korea ke Bahasa Indonesia.\n\n"
            f"Jumlah item: {n}\nID yang wajib ada: {ids}"
        ),
        "user_rules": (
            "- Output hanya terjemahan.\n"
            "- Format wajib: [ID] terjemahan\n"
            "- Semua ID wajib muncul.\n"
            "- Urutan ID wajib sama.\n"
            "- Jangan gabungkan ID.\n"
            "- Jangan hapus ID.\n"
            "- Jangan kosongkan isi terjemahan.\n"
        ),
    },
    "th": {
        "label": "Thailand",
        "suffix_in": "_TH", "suffix_out": "_ID",
        "check_src_chars": False,
        "src_char_re": re.compile(r"[\u0e00-\u0e7f]"),
        "halluc_exact": set(),
        "halluc_contains": set(),
        "system": """Kamu adalah penerjemah subtitle profesional dari Bahasa Thailand ke Bahasa Indonesia.

Aturan WAJIB:
1. Terjemahkan subtitle Thailand ke Bahasa Indonesia yang natural dan enak dibaca.
2. Pertahankan makna, konteks, nada bicara, dan emosi.
3. Jangan membuat ringkasan.
4. Jangan menambah informasi baru.
5. Jangan menghapus baris.
6. Jangan menggabungkan dua ID menjadi satu.
7. Jangan memecah satu ID menjadi beberapa ID.
8. Output HARUS memakai format yang sama:
   [001] terjemahan
   [002] terjemahan
9. Semua ID input HARUS muncul di output.
10. Jangan tulis penjelasan, catatan, markdown, atau komentar.
11. Jika input berupa ekspresi pendek, terjemahkan singkat dan natural sesuai konteks.
12. Jangan pernah mengosongkan terjemahan setelah ID.
13. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
14. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.
""",
        "user_header": lambda n, ids: (
            f"Terjemahkan subtitle berikut dari Bahasa Thailand ke Bahasa Indonesia.\n\n"
            f"Jumlah item: {n}\nID yang wajib ada: {ids}"
        ),
        "user_rules": (
            "- Output hanya terjemahan.\n"
            "- Format wajib: [ID] terjemahan\n"
            "- Semua ID wajib muncul.\n"
            "- Urutan ID wajib sama.\n"
            "- Jangan gabungkan ID.\n"
            "- Jangan hapus ID.\n"
            "- Jangan kosongkan isi terjemahan.\n"
        ),
    },
    "tl": {
        "label": "Filipina",
        "suffix_in": "_TL", "suffix_out": "_ID",
        "check_src_chars": False,
        "src_char_re": re.compile(r"[a-zA-ZñÑ]"),
        "halluc_exact": set(),
        "halluc_contains": set(),
        "system": """Kamu adalah penerjemah subtitle profesional dari Bahasa Filipina (Tagalog) ke Bahasa Indonesia.

Aturan WAJIB:
1. Terjemahkan subtitle Filipina ke Bahasa Indonesia yang natural dan enak dibaca.
2. Pertahankan makna, konteks, nada bicara, dan emosi.
3. Jangan membuat ringkasan.
4. Jangan menambah informasi baru.
5. Jangan menghapus baris.
6. Jangan menggabungkan dua ID menjadi satu.
7. Jangan memecah satu ID menjadi beberapa ID.
8. Output HARUS memakai format yang sama:
   [001] terjemahan
   [002] terjemahan
9. Semua ID input HARUS muncul di output.
10. Jangan tulis penjelasan, catatan, markdown, atau komentar.
11. Jika input berupa ekspresi pendek, terjemahkan singkat dan natural sesuai konteks.
12. Jangan pernah mengosongkan terjemahan setelah ID.
13. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
14. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.
""",
        "user_header": lambda n, ids: (
            f"Terjemahkan subtitle berikut dari Bahasa Filipina ke Bahasa Indonesia.\n\n"
            f"Jumlah item: {n}\nID yang wajib ada: {ids}"
        ),
        "user_rules": (
            "- Output hanya terjemahan.\n"
            "- Format wajib: [ID] terjemahan\n"
            "- Semua ID wajib muncul.\n"
            "- Urutan ID wajib sama.\n"
            "- Jangan gabungkan ID.\n"
            "- Jangan hapus ID.\n"
            "- Jangan kosongkan isi terjemahan.\n"
        ),
    },
    "ru": {
        "label": "Rusia",
        "suffix_in": "_RU", "suffix_out": "_ID",
        "check_src_chars": False,
        "src_char_re": re.compile(r"[\u0400-\u04ff]"),
        "halluc_exact": set(),
        "halluc_contains": set(),
        "system": """Kamu adalah penerjemah subtitle profesional dari Bahasa Rusia ke Bahasa Indonesia.

Aturan WAJIB:
1. Terjemahkan subtitle Rusia ke Bahasa Indonesia yang natural dan enak dibaca.
2. Pertahankan makna, konteks, nada bicara, dan emosi.
3. Jangan membuat ringkasan.
4. Jangan menambah informasi baru.
5. Jangan menghapus baris.
6. Jangan menggabungkan dua ID menjadi satu.
7. Jangan memecah satu ID menjadi beberapa ID.
8. Output HARUS memakai format yang sama:
   [001] terjemahan
   [002] terjemahan
9. Semua ID input HARUS muncul di output.
10. Jangan tulis penjelasan, catatan, markdown, atau komentar.
11. Jika input berupa ekspresi pendek, terjemahkan singkat dan natural sesuai konteks.
12. Jangan pernah mengosongkan terjemahan setelah ID.
13. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
14. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.
""",
        "user_header": lambda n, ids: (
            f"Terjemahkan subtitle berikut dari Bahasa Rusia ke Bahasa Indonesia.\n\n"
            f"Jumlah item: {n}\nID yang wajib ada: {ids}"
        ),
        "user_rules": (
            "- Output hanya terjemahan.\n"
            "- Format wajib: [ID] terjemahan\n"
            "- Semua ID wajib muncul.\n"
            "- Urutan ID wajib sama.\n"
            "- Jangan gabungkan ID.\n"
            "- Jangan hapus ID.\n"
            "- Jangan kosongkan isi terjemahan.\n"
        ),
    },
}


# ─────────────────────────────────────────────
#  UI HELPERS
# ─────────────────────────────────────────────

def banner(rotator: gu.KeyRotator, src_lang: str):
    cfg = SRC_LANG_CONFIGS[src_lang]
    print(f"""
{gu.CY}{gu.B}╔══════════════════════════════════════════════╗
║  🌐  Groq Subtitle Translator (GPT-OSS)   ║
║  Model  : {MODEL:<33}║
║  Input  : {cfg['label']} → Indonesia           ║
║  Keys   : {len(rotator):<2} akun                          ║
╚══════════════════════════════════════════════╝{gu.R}
""")


# ─────────────────────────────────────────────
#  SRT PARSER
# ─────────────────────────────────────────────

def parse_srt(path: Path):
    content = path.read_text(encoding="utf-8", errors="replace")
    blocks = []
    parts = re.split(r"\n\s*\n", content.strip())
    for raw in parts:
        lines = raw.strip().splitlines()
        if len(lines) < 3:
            continue
        try:
            idx = int(lines[0].strip())
        except ValueError:
            continue
        if "-->" not in lines[1]:
            continue
        ts = lines[1].split("-->")
        if len(ts) != 2:
            continue
        text = "\n".join(lines[2:]).strip()
        if not text:
            continue
        blocks.append({"index": idx, "start": ts[0].strip(),
                       "end": ts[1].strip(), "text": text})
    return blocks


# ─────────────────────────────────────────────
#  FILTER HALUSINASI
# ─────────────────────────────────────────────

def is_hallucination(text: str, duration_sec: float, src_lang: str) -> tuple:
    cfg = SRC_LANG_CONFIGS[src_lang]
    t = text.strip()
    if t in cfg["halluc_exact"]:
        return True, f"exact hallucination: {t!r}"
    for frag in cfg["halluc_contains"]:
        if frag in t:
            return True, f"contains hallucination: {frag!r}"
    rep = gu.repeated_phrase(t)
    if rep:
        return True, f"frasa berulang: {rep[:30]!r}"
    if len(t) <= 2 and not cfg["src_char_re"].search(t):
        return True, f"terlalu pendek tanpa karakter {cfg['label']}: {t!r}"
    if src_lang != "en":
        reason = gu.check_latin_junk(t, cfg["src_char_re"], src_lang)
        if reason:
            return True, reason
    if duration_sec >= 20.0 and len(t) <= 25:
        return True, f"teks pendek ({len(t)} char) di segment panjang ({duration_sec:.0f}s)"
    return False, ""


# ─────────────────────────────────────────────
#  OUTPUT CLEANER
# ─────────────────────────────────────────────

def strip_common_junk(raw: str) -> str:
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    raw = re.sub(r"^```(?:text|txt|srt)?\s*", "", raw, flags=re.I)
    raw = re.sub(r"\s*```$", "", raw.strip())
    return raw.strip()


def parse_id_output(raw: str, expected_ids):
    raw = strip_common_junk(raw)
    found = {}
    pattern = re.compile(
        r"^\s*\[?(\d{1,4})\]?\s*[:：\-–—.)]?\s*(.*?)\s*(?=^\s*\[?\d{1,4}\]?\s*[:：\-–—.)]?\s*|\Z)",
        re.M | re.S
    )
    for m in pattern.finditer(raw):
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        text = re.sub(r"\n+", " ", m.group(2).strip()).strip()
        text = text.strip("\"'""''")
        if text:
            found[num] = text
    result = []
    for eid in expected_ids:
        text = found.get(eid)
        if not text:
            return None
        result.append(text)
    return result


def fallback_parse_plain_lines(raw: str, expected_count: int):
    raw = strip_common_junk(raw)
    cleaned = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^(berikut|terjemah|hasil|catatan|note|here|translation)", line.lower()):
            continue
        line = re.sub(r"^\s*\[?\d{1,4}\]?\s*[:：\-–—.)]\s*", "", line).strip()
        if line:
            cleaned.append(line)
    return cleaned if len(cleaned) == expected_count else None


# ─────────────────────────────────────────────
#  GROQ REQUEST
# ─────────────────────────────────────────────

def groq_request(rotator: gu.KeyRotator, messages, max_tokens=4096):
    max_attempts = max(1, len(rotator) * 2)
    client, key_idx = rotator.current()
    for _ in range(max_attempts):
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=messages,
                temperature=0.3, top_p=0.9, max_tokens=max_tokens,
            )
            return resp.choices[0].message.content.strip()
        except gu.AllKeysExhausted:
            raise
        except Exception as e:
            err = str(e).lower()
            is_ratelimit = "429" in err or "rate_limit" in err or "too many requests" in err
            is_daily = "daily" in err or "quota" in err or "limit exceeded" in err
            is_invalid = "401" in err or "403" in err or "invalid" in err or "auth" in err
            if is_daily or is_ratelimit:
                label = "Daily/quota" if is_daily else "Rate"
                print(f"\n   {gu.YL}!  {label} limit {rotator.label()}.{gu.R}", flush=True)
                rotator.mark_rate_limited(key_idx, wait_sec=65)
                client, key_idx = rotator.next()
            elif is_invalid:
                print(f"\n   {gu.RD}x  {rotator.label()} invalid/expired.{gu.R}", flush=True)
                rotator.mark_rate_limited(key_idx, wait_sec=9999)
                client, key_idx = rotator.next()
            else:
                print(f"\n   {gu.YL}!  Error ({rotator.label()}): {e} - retry 5s...{gu.R}", flush=True)
                time.sleep(5)
    return None


# ─────────────────────────────────────────────
#  TRANSLATE BATCH
# ─────────────────────────────────────────────

def make_prompt(batch_items, src_lang: str):
    cfg = SRC_LANG_CONFIGS[src_lang]
    lines = [f"[{item['id']:03d}] {item['text'].replace(chr(10),' ').strip()}"
             for item in batch_items]
    joined = "\n".join(lines)
    ids_text = ", ".join(f"[{x['id']:03d}]" for x in batch_items)
    user_prompt = (
        cfg["user_header"](len(batch_items), ids_text)
        + "\n\nAturan output:\n"
        + cfg["user_rules"]
        + f"\nInput:\n{joined}\n"
    )
    return [
        {"role": "system", "content": cfg["system"]},
        {"role": "user", "content": user_prompt},
    ]


class BatchTranslateFailed(Exception):
    pass


def translate_batch(rotator: gu.KeyRotator, texts, global_start_index, src_lang: str):
    cfg = SRC_LANG_CONFIGS[src_lang]
    batch_items = [
        {"id": offset + 1, "global_index": global_start_index + offset, "text": text}
        for offset, text in enumerate(texts)
    ]
    expected_ids = [x["id"] for x in batch_items]
    messages = make_prompt(batch_items, src_lang)
    last_raw = ""
    for attempt in range(1, MAX_RETRY_BAD_FORMAT + 1):
        raw = groq_request(rotator, messages)
        last_raw = raw or ""
        if raw is None:
            break
        parsed = parse_id_output(raw, expected_ids)
        if parsed is None:
            parsed = fallback_parse_plain_lines(raw, len(texts))
        if parsed is not None and len(parsed) == len(texts):
            return parsed
        got_lines = len([l for l in raw.splitlines() if l.strip()])
        print(
            f"\n   {gu.YL}!  Format salah (terbaca {got_lines}, expected {len(texts)}) "
            f"- retry {attempt}/{MAX_RETRY_BAD_FORMAT}...{gu.R}", flush=True,
        )
        messages = [
            {"role": "system", "content": cfg["system"]},
            {"role": "user", "content": (
                "Output sebelumnya SALAH karena ID/baris tidak lengkap.\n"
                "Ulangi dari input asli. WAJIB keluarkan semua ID.\n\n"
                + make_prompt(batch_items, src_lang)[1]["content"]
            )},
        ]
        time.sleep(1)
    msg = (f"Batch baris global {global_start_index+1}-"
           f"{global_start_index+len(texts)} gagal diterjemahkan.")
    if MARK_FAILED_IN_OUTPUT:
        print(f"\n   {gu.RD}x  {msg} Ditandai [BELUM_DITERJEMAHKAN].{gu.R}", flush=True)
        return [f"[BELUM_DITERJEMAHKAN] {t}" for t in texts]
    raise BatchTranslateFailed(msg)


# ─────────────────────────────────────────────
#  PROOFREAD PASS (QA konteks)
# ─────────────────────────────────────────────

PROOFREAD_CHUNK = 100
PROOFREAD_OVERLAP = 5

PROOFREAD_SYSTEM = """Kamu adalah editor subtitle Indonesia yang memeriksa kualitas terjemahan.

Aturan WAJIB:
1. Baca semua baris secara berurutan sebagai satu alur adegan.
2. Perbaiki baris yang:
   - tidak masuk akal atau tidak nyambung dengan baris di sekitarnya,
   - masih mengandung kata asing / bahasa sumber,
   - hasil salah dengar / salah terjemah yang bisa ditebak dari konteks.
3. Baris yang sudah benar TULIS ULANG PERSIS SAMA, jangan diubah.
4. Jangan menambah, menghapus, atau menggabungkan baris.
5. Jangan tulis penjelasan atau komentar.
6. Output HARUS format: [ID] teks
7. Semua ID input WAJIB muncul di output.
8. Jangan menghaluskan atau menghapus kata vulgar/kasar — pertahankan kadar
   vulgar yang sama. "penis", "vagina", "kontol", "memek" JANGAN diganti jadi
   "alat kelamin" atau kata klinis, dan JANGAN dihapus.
9. Pertahankan nama orang, nama panggilan, dan istilah khusus apa adanya
   (mis. Hinokori, Kan-chan, Yo-kko, Oki, bidet, futon) — jangan dihapus atau
   diganti kata umum.
10. Jangan mengubah kata yang sudah benar hanya untuk gaya; fokus perbaiki
    baris yang salah dengar / tidak nyambung dengan konteks. Perubahan
    minimal lebih baik daripada rombak besar."""

# ponytail: glossary tambahan per bahasa sumber (hanya yang punya istilah khusus;
# ja dulu, tambah kalau bahasa lain butuh).
PROOFREAD_EXTRA = {
    "ja": """

Glossary untuk adegan dewasa (WAJIB konsisten):
- チンポ / チンコ / ちんちん / おちんちん / ペニス / チン → penis (JANGAN "alat kelamin")
- Varian salah dengar Whisper: シンポ / キンコ / きんじん / イチンポ → penis
- クリ / くり → klitoris
- マンコ / まんこ / おまんこ → memek
- ペニマンコ → penis dan memek
- オシュレット / オシュレト → bidet
- Kata vulgar diterjemahkan natural, jangan klinis, jangan dihapus.""",
}

# ponytail: aksara sumber non-Latin (ja/zh/ko/th/ru). En/tl Latin → scan N/A.
NON_LATIN_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7a3\u0e00-\u0e7f\u0400-\u04ff]"
)


def _words(t: str) -> set:
    return set(re.findall(r"[A-Za-z0-9]+", t.lower()))


def is_drastic_change(old: str, new: str) -> bool:
    """Perubahan besar = sebagian besar kata sumber hilang/ganti. Indikator
    garble/halusinasi yang diperbaiki konteks, bukan sekadar normalisasi gaya.
    Baris pendek (<6 char) diabaikan — ganti satu-dua kata bukan pola garble."""
    if old == new:
        return False
    if len(old.strip()) < 6:
        return False
    o = _words(old)
    if not o:
        return True
    return len(o & _words(new)) / len(o) < 0.4


def proofread_srt(rotator: gu.KeyRotator, blocks: list, src_lang: str = "") -> list:
    if not blocks:
        return []
    system = PROOFREAD_SYSTEM + PROOFREAD_EXTRA.get(src_lang, "")
    step = PROOFREAD_CHUNK - PROOFREAD_OVERLAP
    chunks = [blocks[s:s + PROOFREAD_CHUNK] for s in range(0, len(blocks), step)]
    changes = []
    candidates = []
    for ci, chunk in enumerate(chunks):
        items = [{"id": i + 1, "text": b["text"]} for i, b in enumerate(chunk)]
        lines = "\n".join(
            f"[{it['id']:03d}] {it['text'].replace(chr(10), ' ').strip()}" for it in items
        )
        ids_text = ", ".join(f"[{x['id']:03d}]" for x in items)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                f"Periksa dan perbaiki subtitle Indonesia berikut "
                f"(chunk {ci+1}/{len(chunks)}).\n"
                f"Jumlah item: {len(items)}\n"
                f"ID yang wajib ada: {ids_text}\n\n"
                f"Input:\n{lines}\n"
            )},
        ]
        parsed = None
        for _ in range(2):
            raw = groq_request(rotator, messages)
            if not raw:
                break
            parsed = parse_id_output(raw, list(range(1, len(items) + 1)))
            if parsed is not None:
                break
            time.sleep(1)
        if parsed is None:
            print(f"\n   {gu.YL}!  Proofread chunk {ci+1}: parse gagal, chunk dibiarkan.{gu.R}")
            continue
        for i, newtext in enumerate(parsed):
            old = chunk[i]["text"]
            if old != newtext:
                if is_drastic_change(old, newtext):
                    candidates.append((chunk[i]["start"], old, newtext))
                chunk[i]["text"] = newtext
                changes.append((chunk[i]["start"], old, newtext))
    if candidates:
        print(f"\n   {gu.CY}Kandidat perubahan drastis ({len(candidates)}):{gu.R}")
        print(f"   {gu.DM}periksa pola garble/halusinasi baru; tambah ke frasa kalau perlu.{gu.R}")
        for start, old, new in candidates[:10]:
            print(f"      {start}  {old[:50]!r} \u2192 {new[:50]!r}")
        if len(candidates) > 10:
            print(f"      ... dan {len(candidates)-10} lainnya")
    # ponytail: jaring pengaman — blok yang masih berisi aksara bahasa sumber
    # (sisa dari chunk parse gagal) dilaporkan, biar tidak lolos diam-diam.
    leftover = [b for b in blocks if NON_LATIN_RE.search(b["text"])]
    if leftover:
        print(f"\n   {gu.RD}!  {len(leftover)} blok masih mengandung aksara bahasa sumber:{gu.R}")
        for b in leftover[:10]:
            print(f"      {b['start']}  {b['text'][:60]!r}")
        if len(leftover) > 10:
            print(f"      ... dan {len(leftover)-10} lainnya")
    return changes


def write_proofread_log(srt_path: Path, changes: list) -> Path:
    log_path = Path(str(srt_path) + ".proofread.log")
    lines = [f"{start}  {old!r} \u2192 {new!r}" for start, old, new in changes]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


# ─────────────────────────────────────────────
#  TRANSLATE FILE
# ─────────────────────────────────────────────

def translate_file(rotator: gu.KeyRotator, input_path: Path, output_path: Path,
                   src_lang: str, proofread: bool = True) -> dict:
    cfg = SRC_LANG_CONFIGS[src_lang]
    blocks = parse_srt(input_path)
    if not blocks:
        print(f"   {gu.RD}x  File kosong atau format tidak valid{gu.R}")
        return {"total": 0, "failed_marked": 0, "src_chars_left": 0}
    filtered_out = []
    clean_blocks = []
    for b in blocks:
        dur = gu.srt_duration_sec(b["start"], b["end"])
        halluc, reason = is_hallucination(b["text"], dur, src_lang)
        if halluc:
            filtered_out.append((b["index"], b["text"], reason))
        else:
            clean_blocks.append(b)
    if filtered_out:
        print(f"   {gu.YL}!  {len(filtered_out)} blok dibuang (halusinasi):{gu.R}")
        for idx, txt, reason in filtered_out[:10]:
            print(f"      #{idx}: {txt[:60]!r}  \u2190 {reason}")
        if len(filtered_out) > 10:
            print(f"      ... dan {len(filtered_out)-10} lainnya")
    blocks = clean_blocks
    total = len(blocks)

    # ponytail: resume — reuse terjemahan lama yg valid, cuma terjemah yg gagal/baru.
    # Key = start timestamp (stabil), BUKAN index: output SRT di-renumber 1..N setiap
    # tulis, jadi index output tak sejajar dgn index input saat ada blok dibuang
    # (halusinasi) — pakai index menyebabkan reuse salah blok setelah gap pertama.
    prev = {}
    if output_path.exists():
        for pb in parse_srt(output_path):
            if not str(pb["text"]).startswith("[BELUM_DITERJEMAHKAN]"):
                prev[pb["start"]] = pb["text"]

    texts, need_idx = [], []
    for i, b in enumerate(blocks):
        if b["start"] in prev:
            texts.append(prev[b["start"]])   # reuse hasil lama yg valid
        else:
            texts.append(b["text"])
            need_idx.append(i)

    failed_marked = 0
    if need_idx:
        results_buf = {}
        n_need = len(need_idx)
        n_batch = (n_need + BATCH_SIZE - 1) // BATCH_SIZE
        for bi in range(n_batch):
            s = bi * BATCH_SIZE
            e = min(s + BATCH_SIZE, n_need)
            batch = [blocks[need_idx[k]]["text"] for k in range(s, e)]
            print(
                f"\r   [{cfg['label']} \u2192 Indonesia] "
                f"Batch {bi+1}/{n_batch}  "
                f"({s+1}-{e}/{n_need})  "
                f"{bi*100//n_batch}%  "
                f"[{rotator.label()}]     ",
                end="", flush=True,
            )
            translated = translate_batch(rotator, batch, s, src_lang)
            failed_marked += sum(1 for x in translated if str(x).startswith("[BELUM_DITERJEMAHKAN]"))
            for off, t in enumerate(translated):
                texts[need_idx[s + off]] = t
            if bi < n_batch - 1:
                time.sleep(REQUEST_DELAY)
        print(f"\r{' '*90}\r", end="")
    else:
        print(f"   {gu.DM}Resume: semua baris sudah diterjemahkan, skip.{gu.R}")

    for i, b in enumerate(blocks):
        b["text"] = texts[i] if texts[i] is not None else f"[BELUM_DITERJEMAHKAN] {b['text']}"
    gu.write_srt(blocks, output_path)
    src_chars_left = 0
    if cfg["check_src_chars"]:
        left = [b for b in blocks if cfg["src_char_re"].search(b.get("text", ""))]
        src_chars_left = len(left)
        if left:
            label = cfg["label"]
            print(f"   {gu.YL}!  Masih ada {src_chars_left} blok mengandung karakter {label}.{gu.R}")
            for b in left[:15]:
                print(f"      #{b['index']}: {b['text'][:90].replace(chr(10),' ')}")
    if proofread and not failed_marked:
        print(f"\n   {gu.CY}QA proofread (scan konteks & perbaiki baris tak nyambung)...{gu.R}")
        t0 = time.time()
        changes = proofread_srt(rotator, blocks, src_lang)
        if changes:
            gu.write_srt(blocks, output_path)
            print(f"   {gu.YL}!  {len(changes)} baris diperbaiki proofread  {gu.fmt_dur(time.time()-t0)}{gu.R}")
        else:
            print(f"   {gu.DM}  Tidak ada baris yang diubah.  {gu.fmt_dur(time.time()-t0)}{gu.R}")
    return {"total": total, "failed_marked": failed_marked, "src_chars_left": src_chars_left}


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    global GROQ_API_KEYS
    parser = argparse.ArgumentParser(
        description="Groq subtitle translator \u2014 multi-bahasa input \u2192 Indonesia"
    )
    parser.add_argument("--input", help="Path SRT yang mau diterjemahkan")
    parser.add_argument("--output", help="Path output *_ID.srt untuk mode --input")
    parser.add_argument(
        "--lang", default="ja",
        help="Bahasa sumber SRT: ja|zh|en|ko|th|tl|ru  [default: ja]",
    )
    parser.add_argument("--force", action="store_true",
                        help="Paksa translate ulang walau output sudah ada")
    parser.add_argument("--no-proofread", action="store_true",
                        help="Lewati QA proofread setelah translate")
    parser.add_argument("--proofread", action="store_true",
                        help="Mode QA: proofread file *_ID.srt yang sudah ada, tulis ulang di tempat")
    args = parser.parse_args()
    GROQ_API_KEYS = gu.load_groq_api_keys()
    valid_keys = [k.strip() for k in GROQ_API_KEYS if gu.valid_key(k)]
    if not valid_keys:
        print(f"{gu.RD}x  Belum ada GROQ_API_KEYS yang valid. Isi GROQ_API_KEYS di .env project.{gu.R}")
        return 1
    GROQ_API_KEYS.clear()
    GROQ_API_KEYS.extend(valid_keys)
    rotator = gu.KeyRotator(GROQ_API_KEYS)

    # ponytail: proofread generik (tidak butuh bahasa sumber) → jalan sebelum
    # validasi --lang supaya bisa: srt.py --proofread --input x_ID.srt
    if args.proofread:
        fp = Path(args.input).expanduser().resolve()
        if not fp.exists():
            print(f"{gu.RD}x  File SRT tidak ditemukan: {fp}{gu.R}")
            return 1
        blocks = parse_srt(fp)
        if not blocks:
            print(f"{gu.RD}x  File kosong atau format tidak valid: {fp}{gu.R}")
            return 1
        print(f"{gu.CY}  QA proofread: {fp.name}{gu.R}  ({len(blocks)} baris)")
        changes = proofread_srt(rotator, blocks)
        if changes:
            gu.write_srt(blocks, fp)
            log_path = write_proofread_log(fp, changes)
            print(f"{gu.GR}\u2714  {len(changes)} baris diperbaiki, file ditulis ulang.{gu.R}")
            print(f"   {gu.DM}Log revisi: {log_path}{gu.R}")
        else:
            print(f"{gu.DM}  Tidak ada baris yang diubah.{gu.R}")
        return 0

    src_lang = args.lang.strip().lower()
    if src_lang not in SRC_LANG_CONFIGS:
        print(f"{gu.RD}x  --lang tidak valid: '{src_lang}'. Pilih: ja | zh | en | ko | th | tl | ru{gu.R}")
        return 1
    cfg_src = SRC_LANG_CONFIGS[src_lang]
    Path(input_folder).mkdir(parents=True, exist_ok=True)
    Path(output_folder).mkdir(parents=True, exist_ok=True)
    banner(rotator, src_lang)
    print(f"{gu.CY}  Urutan pemakaian key:{gu.R}")
    for i in range(len(rotator)):
        print(f"   {i+1}. {rotator.short_key(i)}")
    print()
    print(f"{gu.CY}  Setting:{gu.R}")
    print(f"   Model            : {MODEL}")
    print(f"   Batch size       : {BATCH_SIZE}")
    print(f"   Request delay    : {REQUEST_DELAY}s")
    print(f"   Retry bad format : {MAX_RETRY_BAD_FORMAT}x")
    print(f"   Bahasa sumber    : {cfg_src['label']}")
    print(f"   Bahasa target    : Indonesia")
    print()
    if args.input:
        fp = Path(args.input).expanduser().resolve()
        if not fp.exists():
            print(f"{gu.RD}x  File SRT tidak ditemukan: {fp}{gu.R}")
            return 1
        stem = fp.stem
        for sfx in ("_JA", "_ZH", "_EN"):
            if stem.upper().endswith(sfx):
                stem = stem[:-len(sfx)]
                break
        out = (Path(args.output).expanduser().resolve() if args.output
               else Path(output_folder) / f"{stem}_ID.srt")
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and not args.force:
            print(f"{gu.YL}  Skip: output sudah ada \u2192 {out}{gu.R}")
            return 0
        print(f"{gu.DM}{'-'*50}{gu.R}")
        print(f"{gu.B}[1/1]{gu.R}  {gu.CY}{fp.name}{gu.R}  "
              f"{gu.DM}({fp.stat().st_size/1024:.1f} KB){gu.R}")
        t0 = time.time()
        try:
            info = translate_file(rotator, fp, out, src_lang, proofread=not args.no_proofread)
        except gu.AllKeysExhausted:
            return 1
        except Exception as e:
            print(f"   {gu.RD}x  Error: {e}{gu.R}")
            return 1
        print(f"   {gu.GR}\u2714  Selesai{gu.R}  {info['total']} baris  {gu.fmt_dur(time.time()-t0)}")
        print(f"   Output: {gu.CY}{out}{gu.R}")
        if info["failed_marked"]:
            print(f"   {gu.YL}!  {info['failed_marked']} baris [BELUM_DITERJEMAHKAN].{gu.R}")
        return 0
    suffix_in = cfg_src["suffix_in"]
    pattern = f"*{suffix_in}.srt"
    files = sorted(Path(input_folder).glob(pattern))
    if not files:
        files = sorted(Path(input_folder).glob("*.srt"))
        if not files:
            print(f"{gu.RD}x  Tidak ada file .srt di '{input_folder}'{gu.R}")
            print(f"{gu.YL}!  Untuk mode --lang {src_lang}, cari pola: {pattern}{gu.R}")
            return 1
        print(f"{gu.YL}!  Tidak ada file {pattern}, fallback ke semua .srt{gu.R}\n")
    print(f"{gu.GR}\u2714  Ditemukan {len(files)} file SRT [{cfg_src['label']}]{gu.R}\n")
    for i, f in enumerate(files, 1):
        print(f"   {gu.DM}{i}.{gu.R} {f.name}  {gu.DM}({f.stat().st_size/1024:.1f} KB){gu.R}")
    print()
    success, failed = 0, []
    t_total = time.time()
    for idx, fp in enumerate(files, 1):
        stem = fp.stem
        for sfx in ("_JA", "_ZH", "_EN"):
            if stem.upper().endswith(sfx):
                stem = stem[:-len(sfx)]
                break
        out = Path(output_folder) / f"{stem}_ID.srt"
        if out.exists() and not args.force:
            print(f"{gu.YL}  Skip: {fp.name} \u2192 output sudah ada{gu.R}")
            continue
        print(f"{gu.DM}{'-'*50}{gu.R}")
        print(f"{gu.B}[{idx}/{len(files)}]{gu.R}  {gu.CY}{fp.name}{gu.R}  "
              f"{gu.DM}({fp.stat().st_size/1024:.1f} KB){gu.R}")
        t0 = time.time()
        try:
            info = translate_file(rotator, fp, out, src_lang, proofread=not args.no_proofread)
            success += 1
            print(f"   {gu.GR}\u2714  Selesai{gu.R}  {info['total']} baris  {gu.fmt_dur(time.time()-t0)}")
            print(f"   Output: {gu.CY}{out}{gu.R}")
            if info["failed_marked"]:
                print(f"   {gu.YL}!  {info['failed_marked']} baris [BELUM_DITERJEMAHKAN].{gu.R}")
        except gu.AllKeysExhausted:
            failed.append(fp.name)
            break
        except Exception as e:
            print(f"   {gu.RD}x  Error: {e}{gu.R}")
            failed.append(fp.name)
    elapsed = time.time() - t_total
    print(f"\n{gu.DM}{'='*50}{gu.R}")
    print(f"{gu.B}{gu.CY}  RINGKASAN{gu.R}")
    print(f"  {gu.GR}\u2714  Berhasil : {success} file{gu.R}")
    if failed:
        print(f"  {gu.RD}x  Gagal    : {len(failed)} file{gu.R}")
        for f in failed:
            print(f"     - {f}")
    print(f"\n  Waktu total : {gu.YL}{gu.fmt_dur(elapsed)}{gu.R}")
    print(f"  Output      : {gu.CY}{output_folder}/{gu.R}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
