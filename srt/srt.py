#!/usr/bin/env python3
"""
Groq LLaMA Subtitle Translator — Multi-Language Input → Indonesia
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
MODEL = "llama-3.3-70b-versatile"
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
13. Kalau nama orang, tempat, merek, atau kode tidak perlu diterjemahkan, pertahankan.
14. Utamakan hasil terjemahan yang alami dan enak dibaca dalam Bahasa Indonesia, jangan terjemahkan kata per kata secara kaku.
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
}


# ─────────────────────────────────────────────
#  UI HELPERS
# ─────────────────────────────────────────────

def banner(rotator: gu.KeyRotator, src_lang: str):
    cfg = SRC_LANG_CONFIGS[src_lang]
    print(f"""
{gu.CY}{gu.B}╔══════════════════════════════════════════════╗
║  🌐  Groq LLaMA Subtitle Translator         ║
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
    if len(t) <= 2:
        return True, f"terlalu pendek: {t!r}"
    if src_lang == "ja":
        if gu.LATIN_FRAG_RE.match(t) and len(t) > 6:
            return True, f"murni Latin/Cyrillic: {t!r}"
        latin_words = gu.MIXED_JUNK_RE.findall(t)
        jp_chars = gu.JP_CHAR_RE.findall(t)
        if latin_words and not jp_chars:
            return True, f"kata Latin tanpa konteks JP: {latin_words}"
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

    # ponytail: resume — pakai terjemahan lama yg valid, cuma terjemah yg gagal/marker.
    prev = {}
    if output_path.exists():
        for pb in parse_srt(output_path):
            if not str(pb["text"]).startswith("[BELUM_DITERJEMAHKAN]"):
                prev[pb["index"]] = pb["text"]

    texts, need_idx = [], []
    for i, b in enumerate(blocks):
        if b["index"] in prev:
            texts.append(prev[b["index"]])   # reuse hasil lama yg valid
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
        help="Bahasa sumber SRT: ja (Jepang) | zh (China) | en (Inggris)  [default: ja]",
    )
    parser.add_argument("--force", action="store_true",
                        help="Paksa translate ulang walau output sudah ada")
    args = parser.parse_args()
    src_lang = args.lang.strip().lower()
    if src_lang not in SRC_LANG_CONFIGS:
        print(f"{gu.RD}x  --lang tidak valid: '{src_lang}'. Pilih: ja | zh | en{gu.R}")
        return 1
    cfg_src = SRC_LANG_CONFIGS[src_lang]
    GROQ_API_KEYS = gu.load_groq_api_keys()
    valid_keys = [k.strip() for k in GROQ_API_KEYS if gu.valid_key(k)]
    if not valid_keys:
        print(f"{gu.RD}x  Belum ada GROQ_API_KEYS yang valid. Isi GROQ_API_KEYS di .env project.{gu.R}")
        return 1
    GROQ_API_KEYS.clear()
    GROQ_API_KEYS.extend(valid_keys)
    rotator = gu.KeyRotator(GROQ_API_KEYS)
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
            info = translate_file(rotator, fp, out, src_lang)
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
