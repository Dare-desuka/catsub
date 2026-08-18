# CatSub

CatSub adalah pipeline lokal untuk membuat subtitle Indonesia dari video berbahasa **Jepang, China, Inggris, Korea, Thailand, Filipina, atau Rusia** secara otomatis.

Alurnya sederhana: video masuk → ditranskripsi jadi teks bahasa sumber → diterjemahkan ke Indonesia → (opsional) di-mux jadi file MKV dengan softsub. Semua berjalan lokal di mesin kamu; hanya proses AI (transcribe & translate) yang memanggil **Groq API**.

```
video (ja/zh/en/ko/th/tl/ru)  →  transcribe (Groq Whisper)  →  translate (Groq GPT-OSS)  →  subtitle Indonesia  →  MKV softsub
```

> **Tidak butuh GPU.** Seluruh pemrosesan model (Whisper large-v3 untuk transcribe, GPT-OSS 120B untuk translate) berjalan di server Groq. Di mesin lokal kamu hanya butuh Python + `ffmpeg` untuk memotong audio. Dependency inti cukup satu paket: `groq`.

## Fitur

- **Auto-detect bahasa** sumber (`auto`) atau paksa manual (`ja` / `zh` / `en` / `ko` / `th` / `tl` / `ru`).
- **Transkripsi** dengan Groq Whisper `whisper-large-v3`.
- **Terjemahan** ke Bahasa Indonesia dengan Groq GPT-OSS `openai/gpt-oss-120b`.
- **Rotasi multi API key** — pakai beberapa key sekaligus (round-robin) untuk menghindari rate limit.
- **Resume translate** — kalau sebagian baris gagal diterjemahkan, jalankan ulang dan hanya baris yang gagal yang diproses (baris valid dipakai ulang).
- **Cache transkripsi** — hasil transcribe disimpan, jadi translate yang gagal bisa diulang tanpa transcribe dua kali.
- **Mode aman** — satu video selesai lalu pause untuk review sebelum lanjut.
- **Mode AUTO** — jalan terus tanpa pause (cocok untuk batch / cron).
- **Auto softsub MKV** (`MKV=1`) — gabungkan video asli + subtitle Indonesia jadi `.mkv` (softsub, tanpa re-encode, track bahasa Indonesia default on).
- **Auto-clean ke trash** (`CLEAN=1`) — setelah MKV sukses, kirim video asli & file SRT ke trash (bisa dipulihkan), bukan hapus permanen.

## API Key yang Dibutuhkan

CatSub butuh **Groq API key** (gratis di <https://console.groq.com>).

1. Buat akun di Groq Console, lalu buat API key.
2. Salin file contoh env:

   ```bash
   cp .env.example .env
   ```

3. Isi key di `.env`:

   ```bash
   GROQ_API_KEYS=gsk_xxxxxxxxxxxxxxxx
   ```

   Kamu bisa mengisi **beberapa key** dipisah koma untuk rotasi otomatis:

   ```bash
   GROQ_API_KEYS=gsk_key1,gsk_key2,gsk_key3
   ```

File `.env` bersifat lokal dan tidak ikut ke repository.

## Requirements

- Python 3 dengan `venv` (virtualenv dibuat per modul: `whisper/venv`, `srt/venv`).
- `ffmpeg` dan `ffprobe` tersedia di sistem (untuk memotong audio per-chunk sebelum dikirim ke Groq).
- `gio` (opsional, hanya untuk fitur `CLEAN=1`; fallback ke `rm` kalau tidak ada).
- Koneksi internet (proses AI berjalan di Groq).

## Install

```bash
./install.sh
```

Installer akan membuat venv di `whisper/` dan `srt/`, lalu memasang dependency (`groq`) di masing-masing.

Install manual per modul:

```bash
cd whisper && python3 -m venv venv && venv/bin/pip install -r requirements.txt
cd srt    && python3 -m venv venv && venv/bin/pip install -r requirements.txt
```

## Struktur Folder

```text
catsub/
├── sub.sh                  # pipeline wrapper (transcribe → translate → mux)
├── install.sh              # installer dependency
├── groq_util.py            # kode bersama (API key, rotasi, helper SRT, filter)
├── .env.example            # template API key
├── .gitignore
├── whisper/                # modul transkripsi (video → SRT bahasa sumber)
│   ├── transcribe.py
│   ├── requirements.txt
│   ├── videos/             # ← taruh video mentah di sini
│   └── transcripts/        # hasil transcribe (cache)
├── srt/                    # modul terjemahan (SRT sumber → SRT Indonesia)
│   ├── srt.py
│   ├── requirements.txt
│   ├── queue/              # antrian SRT yang sedang diterjemahkan (temp)
│   └── subtitles/          # hasil SRT Indonesia (_ID.srt)
└── output/                 # hasil MKV softsub (MKV=1)
```

## Cara Pakai

1. Taruh video ke `whisper/videos/`.
2. Jalankan pipeline:

   ```bash
   ./sub.sh
   ```

Pipeline akan:

1. Mendeteksi video di `whisper/videos/`.
2. Transcribe menjadi SRT bahasa sumber di `whisper/transcripts/`.
3. Menyalin satu SRT ke `srt/queue/`.
4. Menerjemahkan menjadi subtitle Indonesia di `srt/subtitles/`.
5. Membersihkan file temp, lalu pause untuk review.

Hasil akhir subtitle ada di `srt/subtitles/` (suffix `_ID.srt`).

## Opsi Pipeline

Semua opsi diatur lewat environment variable:

| Variabel | Nilai | Fungsi |
|----------|-------|--------|
| `FORCE` | `1` | Paksa ulang semua proses (abaikan hasil lama). |
| `AUTO` | `1` | Jalan terus tanpa pause per-video. |
| `SUB_LANG` | `auto`/`ja`/`zh`/`en`/`ko`/`th`/`tl`/`ru` | Bahasa sumber (default `auto`). |
| `MKV` | `1` | Setelah translate, mux jadi `.mkv` softsub di `output/`. |
| `CLEAN` | `1` | Setelah MKV sukses, kirim video + SRT ke trash. Butuh `MKV=1`. |

Contoh:

```bash
# Paksa ulang semua
FORCE=1 ./sub.sh

# Batch tanpa pause
AUTO=1 ./sub.sh

# Paksa bahasa Jepang
SUB_LANG=ja ./sub.sh

# Transcribe + translate + softsub MKV
MKV=1 ./sub.sh

# Softsub, lalu bersihkan sumber ke trash
MKV=1 CLEAN=1 ./sub.sh

# Kombinasi: auto batch, softsub, clean
AUTO=1 MKV=1 CLEAN=1 ./sub.sh
```

### Catatan `CLEAN=1`

`CLEAN=1` hanya berjalan bila `MKV=1` **dan** proses mux berhasil. File dikirim ke trash sistem (`~/.local/share/Trash`) via `gio trash`, jadi bisa dipulihkan lewat file manager atau:

```bash
gio trash --restore ~/.local/share/Trash/files/<nama-file>
```

Kalau mux gagal, tidak ada yang dihapus (sumber tetap aman). Default fitur ini **mati**.

## Jalankan Modul Terpisah

Transkripsi saja:

```bash
cd whisper
source venv/bin/activate
python3 transcribe.py --lang auto --video /path/video.mp4 --output /path/output.srt
```

Bahasa didukung: `auto`, `ja`, `zh`, `en`, `ko`, `th`, `tl`, `ru`.

Terjemahan SRT saja:

```bash
cd srt
source venv/bin/activate
python3 srt.py --lang ja --input /path/input.srt --output /path/output_ID.srt
```

Bahasa sumber didukung: `ja`, `zh`, `en`, `ko`, `th`, `tl`, `ru`. Target selalu Bahasa Indonesia (suffix `_ID.srt`).
