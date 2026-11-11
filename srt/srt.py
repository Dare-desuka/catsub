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
import traceback
import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import groq_util as gu

# ponytail: verbose/json/dry-run globals
VERBOSE = False
JSON_LOG = None
DRY_RUN = False

def _jlog(event: dict):
    if JSON_LOG:
        try:
            Path(JSON_LOG).parent.mkdir(parents=True, exist_ok=True)
            with open(JSON_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass

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
# ponytail: delay khusus proofread — 1 chunk ≈ 3.2k token vs TPM 8k/mnt/key.
# Tanpa jeda+rotasi, 2-3 chunk beruntun mentokkan 1 key lalu merambat ke semua
# key ("limit semua" padahal TPD masih ada). Rotasi proaktif tiap chunk × 7 org
# + 8s jeda ≈ 4.5k token/mnt/key (aman), wall ±8s/chunk.
PROOFREAD_DELAY = 8
MAX_RETRY_BAD_FORMAT = 4
MARK_FAILED_IN_OUTPUT = True
# ponytail: TPD free tier Groq 200k/hari PER AKUN (limit per organisasi, bukan per
# key/request — 7 key beda akun = 7 jatah). Tanpa header sisa harian dari API,
# pemakaian dicatat di ledger (groq_util.USAGE_FILE) dari usage tiap response.
DAILY_TPD = int(os.environ.get("GROQ_DAILY_TPD", "200000"))
# ponytail: window Groq rolling (bukan reset tengah malam) — key yang kena daily
# di-skip N detik sejak hit terakhir, lalu coba lagi (self-correcting; korban
# maks 1 request/key/window bila ternyata masih mentok)
DAILY_SKIP_SEC = int(os.environ.get("GROQ_DAILY_SKIP_SEC", "21600"))

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
17. Kalimat sumber yang terpotong/garble (fragmen tanpa predikat, mis. ねぇ、なんか / もうばあまん) JANGAN dilengkapi jadi kalimat penuh — terjemahkan fragmen apa adanya ("Hei, anu, anu", "Udah, baa…").
18. Subjek nol (zero-pronoun) mengikuti blok sebelumnya — jangan tiba-tiba ganti "kamu" jadi "aku" atau sebaliknya tanpa petunjuk di sumber.

Glossary wajib (konsisten, jangan ganti-ganti):
- オシュレット / オシュレト → bidet (semprot toilet)
- いく / いくいく / いっちゃう / イく / 行っちゃう dalam konteks seks = mau orgasm/climax →
  "aku mau keluar" / "aku mau cum" — JANGAN literal "pergi" atau "menahan"
- チンポ / チンコ / ちんちん / おちんちん / ペニス / チン → penis
- Varian yang sering salah dengar Whisper juga penis: シンポ / キンコ / きんじん / イチンポ → penis
- クリ / くり → klitoris
- マンコ / まんこ / おまんこ → memek
- ペニマンコ → penis dan memek
- おじさん → om (JANGAN "pakcik"/"paman" kaku)
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
            "- Erangan/onomatope JANGAN ditulis ulang romaji.\n"
            "- Fragmen JANGAN dilengkapi jadi kalimat penuh; subjek nol ikut blok sebelumnya.\n"
        ),
    },
    "zh": {
        "label": "China",
        "suffix_in": "_ZH",
        "suffix_out": "_ID",
        "check_src_chars": True,
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
13. Erangan, desahan, dan onomatopoeia (mis. 嗯, 啊, 哈, 喔, 呜, 高潮的呻吟) terjemahkan
    ke padanan Indonesia singkat ("Ah", "Hmm", "...", "-") atau buang — JANGAN tulis ulang
    dalam pinyin/romaji (mis. jangan jadi "Enen", "Aiya", "Wuwu").
14. Yang dipertahankan apa adanya hanya nama orang/tempat/merek/kode asli; onomatope dan erangan
    BUKAN merek, jadi tetap harus diterjemahkan/dibuang.
15. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
16. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.

Glossary wajib (konsisten, jangan ganti-ganti):
- 鸡巴 / 阴茎 / 屌 / 鸡鸡 → penis
- 屄 / 阴道 / 逼 → memek
- 高潮 / 射 / 射精 / 喷 → mau orgasm ("aku mau keluar/cum") — JANGAN literal "menembak"/"menyemprot"
- 呻吟 / 叫床 / 喘息 → erangan/desahan → "Ah", "Hmm" atau buang, jangan romaji
- 做爱 / 性交 / 操 / 干 → berhubungan seks / ngentot (konteks vulgar, jangan klinis "bersetubuh" kaku)
- Kata vulgar diterjemahkan dengan kata vulgar yang natural, JANGAN dibikin
  klinis ("alat kelamin") atau diterjemahkan ngawur.
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
13. Erangan, desahan, dan onomatopoeia (mis. Ah, Oh, Mmm, come) terjemahkan
    ke padanan Indonesia singkat ("Ah", "Hmm", "...", "-") atau buang — JANGAN tulis ulang
    dengan variasi ngawur ("spank cock→memukul zakar" literal kaku).
14. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
15. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.

Glossary wajib (konsisten, jangan ganti-ganti):
- dick / cock / penis / stick (konteks vulgar) → penis (JANGAN "tongkat" atau "zakar" kaku)
- pussy / vagina → memek
- cum / come (konteks seks) → mau keluar/cum — JANGAN literal "datang"
- cum for me / come for me → "keluar untukku/mau cum"
- fuck → kontol/ngentot (natural, jangan sensor)
- moan / groan → erangan → "Ah"/"Hmm" atau buang
- Kata vulgar natural, JANGAN dibikin klinis ("alat kelamin") atau ngawur.
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
        "check_src_chars": True,
        "src_char_re": re.compile(r"[\uac00-\ud7a3]"),
        "halluc_exact": gu.HALLUC_KO_EXACT,
        "halluc_contains": gu.HALLUC_KO_CONTAINS,
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
13. Erangan, desahan, dan onomatopoeia (mis. 아, 어, 응, 흐, 아앙, 신음) terjemahkan
    ke padanan Indonesia singkat ("Ah", "Hmm", "...", "-") atau buang — JANGAN tulis ulang
    dalam romanisasi (mis. jangan jadi "Aang", "Eung").
14. Yang dipertahankan apa adanya hanya nama orang/tempat/merek/kode asli; onomatope dan erangan
    BUKAN merek, jadi tetap harus diterjemahkan/dibuang.
15. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
16. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.

Glossary wajib (konsisten, jangan ganti-ganti):
- 자지 / 고추 / 좆 → penis (JANGAN "alat kelamin")
- 보지 / 질 → memek
- 클리토리스 → klitoris
- 오르가즘 / 사정 / 싸다 → mau orgasm ("aku mau keluar/cum") — JANGAN literal "menembak"
- 신음 / 앓는 소리 → erangan → "Ah"/"Hmm" atau buang
- 섹스 / 성관계 / 따먹다 → ngentot/berhubungan seks (natural)
- Kata vulgar natural, JANGAN klinis atau ngawur.
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
    # ponytail: TUI_QUIET — 1 baris ringkas (panel TUI gantikan box)
    if os.environ.get("TUI_QUIET"):
        print(f"translate {cfg['label']}→Indonesia · {MODEL} · "
              f"{len(rotator)} key · batch {BATCH_SIZE}", flush=True)
        return
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
    filler = gu.has_filler_repetition(t)
    if filler:
        return True, f"kata berulang: {filler!r}"
    if not re.search(r"\w", t):
        return True, f"isi bukan kata (hanya tanda baca): {t!r}"
    if len(t) <= 2 and not cfg["src_char_re"].search(t):
        return True, f"terlalu pendek tanpa karakter {cfg['label']}: {t!r}"
    if src_lang != "en":
        reason = gu.check_latin_junk(t, cfg["src_char_re"], src_lang)
        if reason:
            return True, reason
    if duration_sec >= 20.0 and len(t) <= 25:
        return True, f"teks pendek ({len(t)} char) di segment panjang ({duration_sec:.0f}s)"
    return False, ""


# ponytail: allowlist kata latin pendek yg legit (audit FOFI/GVH + umum).
# Token hasil di luar ini + sumber pendek = echo romaji garble → buang.
ROMAJI_GARBLE_ALLOW = frozenset("""
ah aku anda angkat atas atau ayo baik bangun basahi begini benar berdiri
berhasil berhenti berikan betul bidet boom brr check-in cit cukup dan datang
deg dengan dorong eh enak entah gah goyang ha hari hehe hei hm hmm huh imut
ini istri iya jangan kamar kan karena kau ke keras keren kopi ku kuat kuji-kun
lah lakukan lebih lihat mainan makan makanya makasih masuk memek menanam
mengerti mm mmm mulut mundur nah naik oh ok oke palpasi parah penisku permisi
punggung sedikit segera senang sendi serius silakan sisi sss sudah tadi tambah
tangan tapi tempel terpilih tidak tolong total tunggu tunjukin tutup uh wah wow
ya yo pergi mau apa bisa ada jadi juga sama sini sana akan belum buat untuk
kalau sial gila mesum basah keluar nikmat mantap puas dalam bawah depan cepat
 pelan sakit penis klitoris kontol cum aduh hah hangat kenapa nggak ssh rantai beri coba udara nyaman shh kasar gak
""".split())


def is_romaji_garble(result: str, src: str) -> bool:
    t = (result or "").strip()
    if not t or t.startswith("[BELUM") or " " in t or re.search(r"\d", t):
        return False
    core = re.sub(r"[^A-Za-z]", "", t)
    if not 2 <= len(core) <= 8 or not re.fullmatch(r"[A-Za-z'\-]+", t):
        return False
    if t.lower() in ROMAJI_GARBLE_ALLOW:
        return False
    return len((src or "").strip()) <= 10


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

def classify_groq_error(err: str):
    """429 Groq generik untuk SEMUA limit — beda harian vs menitan cuma di teks.

    ponytail: pesan TPD asli (tertangkap live 2026-09-28): 'Rate limit reached
    for model ... in organization ... on tokens per day (TPD): Limit 200000,
    Used 199361 ...' — TANPA kata 'daily', dan 'rate_limit_exceeded' (underscore,
    bukan 'limit exceeded' spasi). Tanpa cabang 'tokens per day'/'tpd', daily
    lolos sebagai transient → antre 65s sia-sia di key mati.
    Return (is_daily, is_ratelimit, is_invalid)."""
    e = (err or "").lower()
    # ponytail: \b boundary — '413' polos ikut match 'Requested 1413' di pesan
    # TPD asli (kasus nyata 2026-09-28: daily lolos jadi unknown)
    is_ratelimit = (re.search(r"\b429\b", e) is not None or "rate_limit" in e
                    or "too many requests" in e) \
        and "request too large" not in e and re.search(r"\b413\b", e) is None
    is_daily = ("daily" in e or "quota" in e or "limit exceeded" in e
                or "tokens per day" in e or "tpd" in e)
    is_invalid = "401" in e or "403" in e or "invalid" in e or "auth" in e
    return is_daily, is_ratelimit, is_invalid


def groq_request(rotator: gu.KeyRotator, messages, max_tokens=4096):
    max_attempts = max(1, len(rotator) * 2)
    client, key_idx = rotator.current()
    for _ in range(max_attempts):
        try:
            resp = client.chat.completions.create(
                model=MODEL, messages=messages,
                temperature=0.3, top_p=0.9, max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content.strip()
            # ponytail: catat token aktual ke ledger (fallback estimasi kalau usage tak ada)
            try:
                u = resp.usage
                pt, ct = int(u.prompt_tokens or 0), int(u.completion_tokens or 0)
            except Exception:
                pt, ct = 0, 0
            if pt + ct == 0:
                pt = sum(len(m.get("content", "")) for m in messages) // 4
                ct = len(content) // 4
            rotator.note_usage(key_idx, MODEL, pt, ct)
            return content
        except gu.AllKeysExhausted:
            raise
        except Exception as e:
            err = str(e).lower()
            if VERBOSE:
                print(f"\n   {gu.YL}[verbose] Groq raw: {getattr(e,'body',e)!r} status={getattr(e,'status_code','')!r}{gu.R}", flush=True)
                traceback.print_exc()
            _jlog({"event":"groq_error","key":rotator.label(),"error":str(e)})
            # ponytail: 413 "request too large" mengandung substring rate_limit_exceeded —
            # bukan limit key, payload kegedean. Jangan rotasi key sehat.
            is_daily, is_ratelimit, is_invalid = classify_groq_error(err)
            if is_daily or is_ratelimit:
                label = "Daily/quota" if is_daily else "Rate"
                print(f"\n   {gu.YL}!  {label} limit {rotator.label()}.{gu.R}", flush=True)
                if is_daily:
                    # ponytail: daily ≠ transient — marker persisten + cooldown
                    # window rolling (bukan 65s/midnight); tanpa ini tiap proses
                    # baru mengulang 429 sia-sia di key mati
                    gu.mark_exhausted(key_idx, MODEL, DAILY_TPD)
                    rotator.mark_rate_limited(key_idx, wait_sec=DAILY_SKIP_SEC)
                else:
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
        # ponytail: tanpa sumber — biar menu 6 scan ulang tanpa bocor kana ke _ID.srt
        return [f"[BELUM_DITERJEMAHKAN]" for t in texts]
    raise BatchTranslateFailed(msg)


# ─────────────────────────────────────────────
#  PROOFREAD PASS (QA konteks)
# ─────────────────────────────────────────────

# ponytail: 30 bukan 50/100 — TPD free tier Groq 200k/hari; mode berpasangan (sumber+ID)
# + system prompt + max_tokens=4096. 30 baris ≈ 4.2k tokens/request.
# 1483 baris / 25 = ~60 chunk × 4.2k = 252k (masih >200k tapi lebih aman).
PROOFREAD_CHUNK = 30
PROOFREAD_OVERLAP = 5

# ponytail: estimasi token untuk proofread. Free tier Groq = 200k TPD.
# system prompt ≈ 2000 tokens, user prompt ≈ (baris × 80 char / 4) + overhead.
# response ≈ baris × 50 char / 4. Total ≈ baris × 32 tokens + 2000.
# Dengan chunk 30 + overlap 5 → step 25. chunks = ceil(n/25).
def pick_budget_key(rotator: gu.KeyRotator, model: str, est: int):
    """Agregat: total sisa semua key sehat dijumlahkan (eksekusi rotasi
    per-chunk, jadi file boleh lebih besar dari 1 jatah org 200k selama
    total muat). Key = 1 organisasi (7 akun beda = 7 jatah).

    ponytail: peek tanpa sleep — rotator.next() tidur kalau semua cooldown,
    gate tidak boleh menggantung di situ. Return key sehat pertama (dari
    rotator.idx) kalau total muat, None kalau tidak.
    """
    n = len(rotator)
    now = time.time()
    total, first = 0, None
    for off in range(max(1, n)):
        i = (rotator.idx + off) % n
        if now < rotator.cooldowns.get(i, 0):
            continue
        if gu.daily_blocked(i, DAILY_SKIP_SEC) > 0:
            continue  # ponytail: marker daily rolling — jangan bakar request
        if first is None:
            first = i
        total += max(0, DAILY_TPD - gu.ledger_used(i, model))
    return first if first is not None and total >= est else None


def estimate_proofread_tokens(num_lines: int, has_src_map: bool = False) -> int:
    system_tokens = 2200 if has_src_map else 1800  # system + glossary + mode berpasangan
    step = PROOFREAD_CHUNK - PROOFREAD_OVERLAP
    n_chunks = max(1, (num_lines + step - 1) // step)
    # per chunk: user prompt ≈ items × 80 char / 4 + response ≈ items × 50 char / 4
    # items = min(PROOFREAD_CHUNK, remaining)
    tokens_per_chunk = PROOFREAD_CHUNK * 32  # ~2560 tokens per full chunk
    return system_tokens * n_chunks + tokens_per_chunk * n_chunks  # conservative


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
    minimal lebih baik daripada rombak besar.
11. JANGAN perkenalkan aksara Jepang (ひらがな/カタカナ/漢字) atau aksara
    asing lainnya ke dalam output. Output HARUS 100% Bahasa Indonesia.
    Kalau baris asli punya aksara asing, HAPUS aksara itu dari output.
12. Jangan mengubah makna kata yang sudah benar. "hard touch" ≠ "sentuhan hati".
    "ujung" ≠ "wajah". Contoh JA: いく konteks seks ≠ "pergi"; シンポ/キンコ =
    penis bukan nama orang. Kalau tidak yakin tentang arti, JANGAN ubah.
13. Kalau ragu antara ubah atau tidak, JANGAN ubah. Lebih baik biarkan
    terjemahan yang ada daripada salah dan mengubah arti.
14. JANGAN ubah tanda baca, filler (nih/ya/loh), atau pilihan kata searti
    (nggak/tidak, gimana/bagaimana, oke/baik) kalau makna kalimat sama —
    terapkan HANYA perubahan yang memperbaiki makna."""

# ponytail: glossary tambahan per bahasa sumber (hanya yang punya istilah khusus;
# ja/zh/en/ko dulu, tambah kalau bahasa lain butuh).
PROOFREAD_EXTRA = {
    "ja": """

Glossary untuk adegan dewasa (WAJIB konsisten):
- チンポ / チンコ / ちんちん / おちんちん / ペニス / チン → penis (JANGAN "alat kelamin")
- Varian salah dengar Whisper: シンポ / キンコ / きんじん / イチンポ → penis
- クリ / くり → klitoris
- マンコ / まんこ / おまんこ → memek
- ペニマンコ → penis dan memek
- おじさん → om (JANGAN "pakcik")
- オシュレット / オシュレト → bidet
- いく / いっちゃう / イく dalam konteks seks = mau orgasm → "aku mau keluar/mau cum",
  JANGAN literal "pergi" atau "menahan"
- Erangan/desahan/onomatope (ああ, きゅん, わーきゅん, うう, あら, どうどう) → "Ah"/"Hmm" atau buang,
  JANGAN ditulis ulang romaji (kasus nyata DSOD-060: "Ah"→"Ii—, nyo", "gitu gitu"→"dou dou")
- Kata vulgar diterjemahkan natural, jangan klinis, jangan dihapus.""",
    "zh": """

Glossary untuk adegan dewasa ZH (WAJIB konsisten):
- 鸡巴 / 阴茎 / 屌 / 鸡鸡 → penis (JANGAN "alat kelamin")
- 屄 / 阴道 / 逼 → memek
- 高潮 / 射 / 射精 / 喷 → mau orgasm ("aku mau keluar/mau cum"), JANGAN literal "menembak"
- 呻吟 / 叫床 → erangan → "Ah"/"Hmm" atau buang, jangan pinyin
- 做爱 / 性交 / 操 / 干 → ngentot/berhubungan seks (natural, jangan klinis kaku)
- Kata vulgar natural, jangan klinis, jangan dihapus.""",
    "ko": """

Glossary untuk adegan dewasa KO (WAJIB konsisten):
- 자지 / 고추 / 좆 → penis (JANGAN "alat kelamin")
- 보지 / 질 → memek
- 클리토리스 → klitoris
- 오르가즘 / 사정 / 싸다 → mau orgasm ("aku mau keluar/mau cum"), JANGAN literal "menembak"
- 신음 → erangan → "Ah"/"Hmm" atau buang
- 섹스 / 성관계 / 따먹다 → ngentot/berhubungan seks (natural)
- Kata vulgar natural, jangan klinis, jangan dihapus.""",
    "en": """

Glossary untuk adegan dewasa EN (WAJIB konsisten):
- dick / cock / penis / stick (vulgar) → penis (JANGAN "tongkat"/"zakar" kaku)
- pussy / vagina → memek
- cum / come / cum for me → mau keluar/cum ("come for me" → "keluar untukku")
- fuck → kontol/ngentot (natural, jangan sensor)
- moan / groan → erangan → "Ah"/"Hmm" atau buang
- stick (vulgar) → penis, bukan "tongkat" literal
- Kata vulgar natural, JANGAN klinis ("alat kelamin") atau ngawur.""",
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


def _proofread_room(rotator: gu.KeyRotator, model: str, est_need: int) -> bool:
    """Total sisa semua key sehat muat est_need? Cooldown menitan (65 dtk)
    diabaikan — itu pulih sendiri, bukan alasan berhenti awal."""
    # ponytail: agregat lintas-key (eksekusi rotasi per-chunk) — sisa chunk
    # boleh lebih besar dari 1 jatah org selama total muat.
    return sum(max(0, DAILY_TPD - gu.ledger_used(i, model))
               for i in range(len(rotator))
               if gu.daily_blocked(i, DAILY_SKIP_SEC) <= 0) >= est_need


def proofread_srt(rotator: gu.KeyRotator, blocks: list, src_lang: str = "",
                  src_map=None, save_path=None) -> list:
    if not blocks:
        return []
    system = PROOFREAD_SYSTEM + PROOFREAD_EXTRA.get(src_lang, "")
    if src_map:
        system += ("\nMODE BERPASANGAN: setiap item menampilkan sumber asli lalu terjemahan "
                   "Indonesia. Bandingkan keduanya — kalau maknanya BEDA (salah arti, terbalik, "
                   "atau literal yang salah), perbaiki terjemahannya sesuai sumber. Jangan ubah "
                   "terjemahan yang sudah benar hanya karena gaya. Output HANYA terjemahan "
                   "Indonesia — JANGAN tulis ulang baris sumber atau prefix (sumber).")
    step = PROOFREAD_CHUNK - PROOFREAD_OVERLAP
    chunks = [blocks[s:s + PROOFREAD_CHUNK] for s in range(0, len(blocks), step)]
    changes = []
    candidates = []
    print(f"   {gu.CY}Engine proofread: Groq{gu.R}")
    for ci, chunk in enumerate(chunks):
        # ponytail: 1 baris progres per chunk (flush) — tanpa ini panel TUI
        # kosong ber-menit2 saat 1 chunk Groq 120b jalan 30-60 detik
        print(f"   [proofread] chunk {ci+1}/{len(chunks)} [{rotator.label()}]",
              flush=True)
        # ponytail: cek budget sisa per chunk — estimasi depan bisa meleset (retry
        # 2x/chunk, mode berpasangan); kehabisan di tengah = hangus total kalau
        # tulis cuma di akhir. Berhenti awal + simpan parsial lebih murah.
        rem_lines = sum(len(c) for c in chunks[ci:])
        if not _proofread_room(rotator, MODEL,
                               estimate_proofread_tokens(rem_lines, bool(src_map))):
            print(f"\n   {gu.YL}!  Budget harian tak cukup untuk {len(chunks)-ci} "
                  f"chunk sisa — berhenti awal, {len(changes)} perubahan tersimpan.{gu.R}",
                  flush=True)
            if save_path:
                try:
                    gu.write_srt(blocks, Path(save_path))
                    if changes:
                        write_proofread_log(Path(save_path), changes)
                except Exception:
                    pass  # ponytail: best-effort
            raise gu.AllKeysExhausted()
        items = [{"id": i + 1,
                  "src": (src_map or {}).get(b["start"], ""),
                  "text": b["text"]} for i, b in enumerate(chunk)]
        lines = "\n".join(
            f"[{it['id']:03d}] " + (f"(sumber) {it['src']}\n      " if it["src"] else "")
            + it['text'].replace(chr(10), ' ').strip() for it in items
        )
        ids_text = ", ".join(f"[{x['id']:03d}]" for x in items)
        user_prompt = (
            f"Periksa dan perbaiki subtitle Indonesia berikut "
            f"(chunk {ci+1}/{len(chunks)}).\n"
            f"Jumlah item: {len(items)}\n"
            f"ID yang wajib ada: {ids_text}\n"
            f"Patuhi glossary system prompt; kata asing JANGAN ditulis ulang romaji.\n\n"
            f"Input:\n{lines}\n"
        )
        parsed = None
        for _ in range(2):
            # Groq: kirim sebagai messages (ponytail: gemini dihapus, groq only)
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ]
            raw = groq_request(rotator, messages)
            if not raw:
                break
            parsed = parse_id_output(raw, list(range(1, len(items) + 1)))
            if parsed is None:
                parsed = fallback_parse_plain_lines(raw, len(items))
            if parsed is not None:
                break
            time.sleep(1)
        if parsed is None:
            print(f"\n   {gu.YL}!  Proofread chunk {ci+1}: parse gagal, chunk dibiarkan.{gu.R}")
            continue
        for i, newtext in enumerate(parsed):
            # ponytail: model kadang meniru format input "(sumber) JA <spasi2> ID"
            # ke output — buang prefix-nya, sisakan terjemahan Indonesia saja.
            newtext = re.sub(r"^.*\(sumber\).*?\s{2,}", "", newtext, flags=re.S)
            # ponytail: model juga meniru ID input "[001]" ke output (parse butuh
            # ID, jadi prompt tetap bernomor) — buang di sini; habis dibuang
            # kosong → tolak perubahan.
            newtext = re.sub(r"^\[\d{1,4}\]\s*", "", newtext).strip()
            old = chunk[i]["text"]
            if old != newtext:
                if not newtext:
                    continue  # skip, pertahankan old
                # ponytail: anti-noise — himpunan kata identik (cuma pindah
                # tanda baca/filler) = makna sama → tolak.
                ow = _words(old)
                if ow and ow == _words(newtext):
                    continue  # skip, pertahankan old
                # ponytail: anti-regresi glossary — old mengandung istilah kunci
                # tapi new menghilangkannya (mis. bidet→"Beritahu aku",
                # cum→"pergi", dibobol→gagap di START-543) → tolak.
                # ponytail: verba vulgar (cum/bobol/entot/gasak) + part tubuh
                # (pantat/payudara) ikut dilindungi; "bokong" TIDAK masuk —
                # bokong→payudara itu fix bagus (おっぱい), jangan diblokir.
                if any(t in old.lower() and t not in newtext.lower()
                        for t in ("bidet", "penis", "memek", "klitoris", "kontol",
                                  "cum", "bobol", "entot", "gasak",
                                  "pantat", "payudara")):
                    continue  # skip, pertahankan old
                # ponytail: anti-regresi — kalau hasil proofread mengandung aksara
                # sumber (model memperkenalkan JP/ZH/KO ke output ID), tolak perubahan.
                if src_lang == "ja" and re.search(r"[\u3040-\u30ff]", newtext):
                    continue  # skip, pertahankan old
                if src_lang == "zh" and re.search(r"[\u4e00-\u9fff]", newtext):
                    continue  # skip, pertahankan old
                if src_lang == "ko" and re.search(r"[\uac00-\ud7a3]", newtext):
                    continue  # skip, pertahankan old
                if is_drastic_change(old, newtext):
                    candidates.append((chunk[i]["start"], old, newtext))
                chunk[i]["text"] = newtext
                changes.append((chunk[i]["start"], old, newtext))
        # ponytail: tulis progresif per chunk (cermin checkpoint translate) —
        # run mati di tengah tidak menghanguskan chunk yang sudah dibayar
        if save_path:
            try:
                gu.write_srt(blocks, Path(save_path))
            except Exception:
                pass  # ponytail: best-effort
        # ponytail: pacing TPM + rotasi proaktif — tiap chunk pindah org berikut
        # (sebar 8k TPM/mnt ke 7 jatah) lalu jeda; tanpa ini 1 org dipalu terus
        if ci < len(chunks) - 1:
            now = time.time()
            # ponytail: next() tidur bila SEMUA cooldown (bisa jam-an untuk
            # marker daily) — rotasi hanya bila ada alternatif sehat, sisanya
            # biarkan jalur error yang menangani seperti sebelumnya
            if any(now >= rotator.cooldowns.get(i, 0)
                   for i in range(len(rotator)) if i != rotator.idx):
                rotator.next(quiet=True)
            time.sleep(PROOFREAD_DELAY)
    # ponytail: proofread menyerah ("Tidak dapat dipahami") atau baris tanpa kata
    # (artefak titik-titik) → buang bloknya, daripada sampah masuk subtitle final.
    junk = [b for b in blocks
            if "Tidak dapat dipahami" in b["text"] or not re.search(r"\w", b["text"])]
    for b in junk:
        changes.append((b["start"], b["text"], "(dibuang)"))
        blocks.remove(b)
    if junk:
        print(f"   {gu.YL}!  {len(junk)} blok dibuang (tak terjemahkan / tanpa kata).{gu.R}")
    if candidates:
        print(f"\n   {gu.CY}Kandidat perubahan drastis ({len(candidates)}):{gu.R}")
        print(f"   {gu.DM}periksa pola garble/halusinasi baru; tambah ke frasa kalau perlu.{gu.R}")
        for start, old, new in candidates[:10]:
            print(f"      {start}  {old[:50]!r} \u2192 {new[:50]!r}")
        if len(candidates) > 10:
            print(f"      ... dan {len(candidates)-10} lainnya")
    # ponytail: jaring pengaman — blok yang masih berisi aksara bahasa sumber
    # ja/zh/ko: langsung jadi BELUM (menu 6 bisa retry), non-ja: lapor saja
    leftover = [b for b in blocks if NON_LATIN_RE.search(b["text"])]
    if leftover:
        if src_lang in ("ja", "zh", "ko"):
            for b in leftover:
                b["text"] = "[BELUM_DITERJEMAHKAN]"
            print(f"\n   {gu.YL}!  {len(leftover)} blok {src_lang} masih beraksara → [BELUM_DITERJEMAHKAN] (retry menu 6){gu.R}")
            changes.extend([(b["start"], b["text"], "[BELUM_DITERJEMAHKAN]") for b in leftover])
        else:
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
                   src_lang: str) -> dict:
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
    # ponytail: dry-run preview tanpa Groq
    if DRY_RUN:
        n_batch = (total + BATCH_SIZE - 1)//BATCH_SIZE if total else 0
        est = total*10
        print(f"   {gu.DM}[dry-run] {input_path.name} {total} blok → {n_batch} batch, est {est} tokens (skip Groq){gu.R}")
        _jlog({"event":"translate_dry_run","input":str(input_path),"total":total,"batches":n_batch,"est_tokens":est,"lang":src_lang})
        return {"total": total, "failed_marked": 0, "src_chars_left": 0}

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
        # ponytail: run gagal (AllKeysExhausted) hanguskan semua batch tanpa checkpoint —
        # kasus nyata 2026-09-29: 2 run gagal (SDJS/NOSKN) bakar ~1.9jt token, nol output.
        # Tulis output parsial tiap batch (sudah-jadi + BELUM sisanya) supaya run berikut
        # resume dari tengah via prev (key = start timestamp, marker BELUM = perlu lagi).
        reused = set(range(len(blocks))) - set(need_idx)
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
            try:
                done = reused | set(need_idx[:e])
                cp = [{"start": b["start"], "end": b["end"],
                       "text": texts[i] if i in done else "[BELUM_DITERJEMAHKAN]"}
                      for i, b in enumerate(blocks)]
                gu.write_srt(cp, output_path)
            except Exception:
                pass  # ponytail: checkpoint best-effort — gagal tulis ≠ gagal job
            if bi < n_batch - 1:
                time.sleep(REQUEST_DELAY)
        print(f"\r{' '*90}\r", end="")
    else:
        print(f"   {gu.DM}Resume: semua baris sudah diterjemahkan, skip.{gu.R}")

    # ponytail: snapshot sumber sebelum tertimpa hasil (untuk cek romaji-garble)
    src_text = {b["index"]: b["text"] for b in blocks}
    for i, b in enumerate(blocks):
        txt = texts[i] if texts[i] is not None else f"[BELUM_DITERJEMAHKAN] {b['text']}"
        # ponytail: buang titik-titik/koma awal artefak garble ('.., umm')
        b["text"] = re.sub(r"^[\s.\u3002\u3001,]+(?=\S)", "", txt)
    # ponytail: aksara sumber tersisa setelah translate — ja murni → BELUM (retry via menu 6), campur → strip
    src_chars_left = 0
    if cfg["check_src_chars"]:
        jp_re = cfg["src_char_re"]
        stripped, marked = 0, 0
        for b in blocks:
            if jp_re.search(b.get("text", "")):
                has_latin = re.search(r"[a-zA-Z]", b["text"])
                if has_latin:
                    b["text"] = jp_re.sub("", b["text"]).strip()
                    b["text"] = re.sub(r"^\s*[,\s]+\s*", "", b["text"])
                    stripped += 1
                else:
                    # ponytail: murni JP = translate menyerah, mark BELUM biar menu 6 bisa retry, bukan bocor kana
                    b["text"] = "[BELUM_DITERJEMAHKAN]"
                    marked += 1
                    src_chars_left += 1
        before = len(blocks)
        blocks[:] = [b for b in blocks if b["text"].strip() and re.search(r"\w", b["text"])]
        actually_removed = before - len(blocks)
        if actually_removed:
            print(f"   {gu.YL}!  {actually_removed} blok buang (aksara sumber tanpa terjemahan){gu.R}")
        if stripped:
            print(f"   {gu.YL}!  {stripped} blok: aksara {cfg['label']} stripped dari terjemahan{gu.R}")
        if marked:
            print(f"   {gu.YL}!  {marked} blok murni {cfg['label']} → [BELUM_DITERJEMAHKAN] (retry menu 6){gu.R}")
            # ponytail: jangan buang BELUM di sini — biarkan tertulis biar sub.sh resume detek grep BELUM
            # tapi jangan hitung sebagai src_chars_left bocor (sudah jadi marker latin)
    # ponytail: romaji-garble sisi hasil (ja/zh/ko, hapus langsung).
    # Groq kadang echo bacaan kana sbg latin (ろべえ→Robe, て→te, すばらしい→Aim);
    # lolos cek sumber karena sumbernya pendek ber-kana.
    # Ceiling: kata legit tak dikenal ikut kebuang (1 baris, minor); tiap drop di-log
    # dgn sumbernya — tambah ke ROMAJI_GARBLE_ALLOW bila salah sasaran. Blok terbuang
    # ikut diterjemahkan ulang tiap rerun (tanpa skip-list persisten).
    if src_lang in ("ja", "zh", "ko"):
        garble_out = [(b["index"], src_text.get(b["index"], ""), b["text"]) for b in blocks
                      if is_romaji_garble(b.get("text", ""), src_text.get(b["index"], ""))]
        if garble_out:
            print(f"   {gu.YL}!  {len(garble_out)} blok dibuang (romaji garble sisi hasil):{gu.R}")
            for idx, src, txt in garble_out[:10]:
                print(f"      #{idx}: {txt[:40]!r}  ← {src[:40]!r}")
            if len(garble_out) > 10:
                print(f"      ... dan {len(garble_out)-10} lainnya")
            drop_ids = {idx for idx, _, _ in garble_out}
            blocks[:] = [b for b in blocks if b["index"] not in drop_ids]
    # ponytail: ID-side filler detection — Groq kadang perkenalkan/kuatkan filler
    # saat translate (mis. いくいく ×6 → "aku mau cum" ×6). Scan ID text, buang
    # blok yang isinya ≥4x kata filler pendek. Beda dari JA-side: ini cek terjemahan.
    from collections import Counter
    ID_FILLER_WORDS = {"um", "ah", "eh", "ya", "iya", "ku", "kan", "kok", "dong", "sih"}
    id_filler_out = []
    for b in blocks:
        words = re.findall(r"\b\w{2,3}\b", b["text"].lower())
        for w, c in Counter(words).items():
            if c >= 4 and w in ID_FILLER_WORDS:
                id_filler_out.append((b["index"], b["text"], w))
                break
    if id_filler_out:
        print(f"   {gu.YL}!  {len(id_filler_out)} blok dibuang (ID filler berulang):{gu.R}")
        for idx, txt, w in id_filler_out[:10]:
            print(f"      #{idx}: {txt[:60]!r}  ← {w!r} ×4+")
        if len(id_filler_out) > 10:
            print(f"      ... dan {len(id_filler_out)-10} lainnya")
        blocked_ids = {idx for idx, _, _ in id_filler_out}
        blocks[:] = [b for b in blocks if b["index"] not in blocked_ids]
    gu.write_srt(blocks, output_path)
    # ponytail: auto-proofread dihapus — QA manual via menu 6 (--proofread) saja
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
    parser.add_argument("--proofread", action="store_true",
                        help="Mode QA: proofread file *_ID.srt yang sudah ada, tulis ulang di tempat")
    parser.add_argument("--verbose", action="store_true", help="Tampilkan raw Groq error & traceback")
    parser.add_argument("--dry-run", action="store_true", help="Preview batch & token tanpa panggil Groq")
    parser.add_argument("--json-log", help="Tulis log JSON terstruktur ke file (append)")
    parser.add_argument("--yes", "-y", action="store_true",
                        help="Skip semua konfirmasi prompt")
    args = parser.parse_args()
    # ponytail: set globals untuk helper
    global VERBOSE, JSON_LOG, DRY_RUN
    VERBOSE = args.verbose
    JSON_LOG = args.json_log
    DRY_RUN = args.dry_run
    if DRY_RUN:
        print(f"   {gu.DM}[dry-run] Preview tanpa panggil Groq{gu.R}")
    GROQ_API_KEYS = gu.load_groq_api_keys()
    valid_keys = [k.strip() for k in GROQ_API_KEYS if gu.valid_key(k)]
    if not valid_keys:
        print(f"{gu.RD}x  Belum ada GROQ_API_KEYS yang valid. Isi GROQ_API_KEYS di .env project.{gu.R}")
        return 1
    GROQ_API_KEYS.clear()
    GROQ_API_KEYS.extend(valid_keys)
    rotator = gu.KeyRotator(GROQ_API_KEYS)
    # ponytail: gagal-cepat bila semua key cooldown lama (marker daily jam-an)
    # — tanpa ini next() tidur ber-jam2 dikira hang (kasus nyata 2026-09-29)
    rotator.max_wait = 300

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
        # ponytail: blok placeholder '.' (artefak garble Whisper) dibuang dulu
        dots = [b for b in blocks if re.fullmatch(r"[.。\s]+", b["text"])]
        if dots:
            blocks = [b for b in blocks if b not in dots]
            gu.write_srt(blocks, fp)
            print(f"{gu.YL}  {len(dots)} blok placeholder '.' dibuang.{gu.R}")
        # mode berpasangan: kalau transkrip sumber ada di cache, ikutkan
        src_map = {}
        stem = fp.stem[:-3] if fp.stem.endswith("_ID") else fp.stem
        src_path = Path(__file__).resolve().parent.parent / "whisper" / "transcripts" / f"{stem}.srt"
        if src_path.exists():
            src_blocks = parse_srt(src_path)
            src_map = {b["start"]: b["text"].replace("\n", " ") for b in src_blocks}
            print(f"{gu.CY}  Mode berpasangan sumber: {src_path.name}{gu.R} ({len(src_map)} baris)")
        else:
            print(f"{gu.DM}  Transkrip sumber tidak ditemukan, scan tanpa pembanding.{gu.R}")
        print(f"{gu.CY}  QA proofread: {fp.name}{gu.R}  ({len(blocks)} baris)")
        # ponytail: infer src_lang dari sisa aksara biar leftover ja/zh/ko → BELUM jalan di menu 6 (standalone src_lang="" sebelumnya cuma lapor)
        inferred = ""
        concat = "\n".join(b["text"] for b in blocks)
        if re.search(r"[\u3040-\u30ff]", concat):
            inferred = "ja"
        elif re.search(r"[\u4e00-\u9fff]", concat):
            inferred = "zh"
        elif re.search(r"[\uac00-\ud7a3]", concat):
            inferred = "ko"
        elif re.search(r"[\u0e00-\u0e7f]", concat):
            inferred = "th"
        elif re.search(r"[\u0400-\u04ff]", concat):
            inferred = "ru"
        if inferred:
            print(f"   {gu.DM}Src infer: {inferred} (dari sisa aksara){gu.R}")
        # ponytail: hard gate budget harian (ganti konfirmasi 90% yang di-bypass
        # --yes). Tolak start kalau total sisa semua key tak muat estimasi —
        # tanpa kirim 1 request pun. Exit 2 = budget habis (bukan error,
        # sub.sh/TUI tampilkan "lanjut besok").
        est_tokens = estimate_proofread_tokens(len(blocks), has_src_map=bool(src_map))
        print(f"   {gu.DM}Estimasi token: ~{est_tokens:,}  (limit harian/akun: {DAILY_TPD:,}){gu.R}")
        picked = pick_budget_key(rotator, MODEL, est_tokens)
        if picked is None:
            print(f"   {gu.RD}x  Budget harian tidak muat (butuh ~{est_tokens:,} token).{gu.R}")
            for i in range(len(rotator)):
                blocked = gu.daily_blocked(i, DAILY_SKIP_SEC)
                if blocked > 0:
                    h, rem = divmod(int(blocked), 3600)
                    print(f"      key #{i+1}: daily habis, coba lagi ±{h}j{rem // 60:02d}m")
                    continue
                used = gu.ledger_used(i, MODEL)
                print(f"      key #{i+1}: terpakai {used:,}, sisa {DAILY_TPD - used:,}")
            print(f"   {gu.DM}Ulangi nanti atau naikkan GROQ_DAILY_TPD.{gu.R}")
            _jlog({"event": "budget_refused", "need": est_tokens, "limit": DAILY_TPD})
            return 2
        if picked != rotator.idx:
            print(f"   {gu.YL}Key #{rotator.idx+1} tidak sehat → pakai key #{picked+1}.{gu.R}")
            rotator.idx = picked
        total_left = sum(max(0, DAILY_TPD - gu.ledger_used(i, MODEL))
                         for i in range(len(rotator))
                         if gu.daily_blocked(i, DAILY_SKIP_SEC) <= 0)
        print(f"   {gu.DM}Budget agregat {len(rotator)} key: butuh ~{est_tokens:,}, "
              f"total sisa {total_left:,} (per org {DAILY_TPD:,}).{gu.R}")
        try:
            changes = proofread_srt(rotator, blocks, src_lang=inferred, src_map=src_map,
                                    save_path=fp)
        except gu.AllKeysExhausted:
            # ponytail: semua key cooldown lama di tengah jalan (marker daily
            # jam-an) — gagal-cepat exit 2, bukan tidur ber-jam2 dikira hang
            print(f"   {gu.RD}x  Semua key cooldown — coba lagi nanti.{gu.R}")
            _jlog({"event": "budget_exhausted_midrun"})
            return 2
        if changes:
            gu.write_srt(blocks, fp)
            log_path = write_proofread_log(fp, changes)
            print(f"{gu.GR}\u2714  {len(changes)} baris diperbaiki, file ditulis ulang.{gu.R}")
            print(f"   {gu.DM}Log revisi: {log_path}{gu.R}")
            for start, old, new in changes:
                old1 = old.replace("\n", " ")
                new1 = new.replace("\n", " ")
                print(f"   {start}  {gu.YL}{old1!r}{gu.R} \u2192 {gu.GR}{new1!r}{gu.R}")
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
    # ponytail: TUI_QUIET — daftar key + setting sudah ringkas di banner 1 baris
    if not os.environ.get("TUI_QUIET"):
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
            info = translate_file(rotator, fp, out, src_lang)
        except gu.AllKeysExhausted:
            return 1
        except Exception as e:
            traceback.print_exc()
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
            info = translate_file(rotator, fp, out, src_lang)
            success += 1
            print(f"   {gu.GR}\u2714  Selesai{gu.R}  {info['total']} baris  {gu.fmt_dur(time.time()-t0)}")
            print(f"   Output: {gu.CY}{out}{gu.R}")
            if info["failed_marked"]:
                print(f"   {gu.YL}!  {info['failed_marked']} baris [BELUM_DITERJEMAHKAN].{gu.R}")
        except gu.AllKeysExhausted:
            failed.append(fp.name)
            break
        except Exception as e:
            traceback.print_exc()
            print(f"   {gu.RD}x  Error: {e}{gu.R}")
            failed.append(fp.name)
    elapsed = time.time() - t_total
    # ponytail: TUI_QUIET — ringkasan 1-2 baris (layar selesai TUI gantikan box)
    if os.environ.get("TUI_QUIET"):
        print(f"SELESAI ok={success} fail={len(failed)} {gu.fmt_dur(elapsed)}")
        for f in failed:
            print(f"   x {f}")
        return 0
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
