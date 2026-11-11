#!/usr/bin/env python3
"""tui.py — TUI catsub (curses, stdlib only).

Front-end interaktif untuk sub.sh TANPA mengubah pipeline: tiap aksi = panggil
./sub.sh yang sama (env MKV/CLEAN/AUTO/FORCE/SUB_LANG/TUI_MODE/TUI_SRTS + arg
video), stdout di-stream ke panel log + progress bar di-parse dari marker yang
sudah dicetak script ([n/8], Batch n/m, SELESAI).

Jalankan:  python3 tui.py
Self-test: python3 tui.py --self-test   (assert parser murni, tanpa curses)
"""
import curses
import glob as globmod
import ast
import os
import queue
import re
import subprocess
import sys
import threading
import time
from collections import deque

ROOT = os.path.dirname(os.path.abspath(__file__))
VIDEO_DIR = os.path.join(ROOT, "whisper", "videos")
SRT_OUT_DIR = os.path.join(ROOT, "srt", "subtitles")
MKV_OUT_DIR = os.path.join(ROOT, "output")
VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv")
LANGS = [("auto", "auto-detect"), ("ja", "Jepang"), ("zh", "China"),
         ("en", "Inggris"), ("ko", "Korea"), ("th", "Thailand"),
         ("tl", "Filipina"), ("ru", "Rusia")]
MIN_H, MIN_W = 24, 80
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ARROW = "→"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"  # ponytail: opsi B braille (kitty render sempurna)
SPINNER_RE = re.compile(r"│.*\.\.\.\s*[-\\|/]\s*$")  # frame spinner bash
FFMPEG_RE = re.compile(r"^frame=\s*\d+.*\b(fps|time)=", re.I)  # progres \r ffmpeg
ERR_RE = re.compile(r"error|fail|gagal|invalid|denied|traceback", re.I)
# ponytail: warna viewer diff — pair 1 biru (sebelum), 2 hijau (sesudah), bg -1
# (ikut transparansi terminal). HAS_COLOR diisi main() bila terminal mampu.
HAS_COLOR = False
C_OLD, C_NEW = 1, 2
CHUNK_RE = re.compile(r"\[(\d+)/(\d+)\][^\n]*?\((\d+)%\)")
BATCH_RE = re.compile(r"Batch (\d+)/(\d+)[^\n]*?(\d+)%")
DONE_RE = re.compile(r"SELESAI|✔\s*Selesai|Mux selesai|SELESAI\s*$")


# ── Helper murni (self-testable, tanpa curses) ──────────────────────────────
def strip_ansi(s):
    return ANSI_RE.sub("", s)


def parse_sel(spec, n):
    """'1,3,7' / '1-5' / ''=semua → index 0-based, dedupe, buang yg invalid."""
    if not (spec or "").strip():
        return list(range(n))
    out = []
    for part in (spec or "").split(","):
        part = part.strip().replace(" ", "")
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            out.extend(range(a - 1, b))
        elif re.fullmatch(r"\d+", part):
            out.append(int(part) - 1)
    seen, res = set(), []
    for i in out:
        if 0 <= i < n and i not in seen:
            seen.add(i)
            res.append(i)
    return res


def parse_progress(line):
    """Baris log → (label, 0-100) atau None. 100 = tahap selesai."""
    m = BATCH_RE.search(line)
    if m:
        d, t, p = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (f"Translate batch {d}/{t}", min(100, p))
    m = re.search(r"chunk (\d+)/(\d+)", line)
    if m:
        # ponytail: baris progres proofread per chunk (tanpa %) → bar dari posisi
        d, t = int(m.group(1)), int(m.group(2))
        return (f"Proofread chunk {d}/{t}", min(99, d * 100 // max(1, t)))
    m = CHUNK_RE.search(line)
    if m:
        d, t, p = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return (f"Transcribe chunk {d}/{t}", min(100, p))
    if DONE_RE.search(line):
        return ("Selesai", 100)
    for key, label in (("Pindah ke key", "Rotasi key"),
                       ("Rate limit", "Rate limit, tunggu"),
                       ("Mux ", "Mux MKV"), ("Transcribe", "Transcribe"),
                       ("Translate", "Translate")):
        if key in line:
            return (label, -1)
    return None


def is_progress_noise(line):
    """Fragmen animasi CLI (spinner bash / progres \\r ffmpeg) → jangan masuk
    panel log. Baris error/gagal selalu lolos walau polanya mirip progres."""
    if ERR_RE.search(line):
        return False
    if SPINNER_RE.search(line):
        return True
    return bool(FFMPEG_RE.search(line))


def expand_paths(lines):
    """Baris path manual → file video valid (~/glob/folder/file, dedupe)."""
    out = []
    for raw in lines:
        p = os.path.expanduser((raw or "").strip())
        if not p:
            continue
        if os.path.isdir(p):
            for f in sorted(globmod.glob(os.path.join(p, "*"))):
                if os.path.isfile(f) and f.lower().endswith(VIDEO_EXTS):
                    out.append(f)
        elif re.search(r"[*?\[]", p):
            for f in sorted(globmod.glob(p)):
                if os.path.isfile(f) and f.lower().endswith(VIDEO_EXTS):
                    out.append(f)
        elif os.path.isfile(p) and p.lower().endswith(VIDEO_EXTS):
            out.append(p)
    seen, res = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            res.append(f)
    return res


def list_videos():
    if not os.path.isdir(VIDEO_DIR):
        return []
    return sorted(f for f in globmod.glob(os.path.join(VIDEO_DIR, "*"))
                  if os.path.isfile(f) and f.lower().endswith(VIDEO_EXTS))


def list_srts():
    if not os.path.isdir(SRT_OUT_DIR):
        return []
    return sorted(globmod.glob(os.path.join(SRT_OUT_DIR, "*_ID.srt")))


def srt_status(path):
    """(video_ada, mkv_ada, ada_log) — cermin select_srt_files di sub.sh."""
    stem = os.path.basename(path)[:-len("_ID.srt")]
    video = any(os.path.isfile(os.path.join(VIDEO_DIR, stem + e))
                for e in VIDEO_EXTS)
    mkv = os.path.isfile(os.path.join(MKV_OUT_DIR, stem + ".mkv"))
    log = os.path.isfile(path + ".proofread.log")
    return video, mkv, log


def parse_proofread_line(line):
    """'TS  {old!r} → {new!r}' → (ts, old, new) tanpa kutip. None bila tak cocok."""
    if ARROW not in line:
        return None
    left, _, right = line.partition(f" {ARROW} ")
    ts, _, old_repr = left.partition("  ")
    if not ts or not old_repr:
        return None
    ts, old_repr = ts.strip(), old_repr.strip()
    new_repr = right.strip()
    try:
        old = ast.literal_eval(old_repr)
        new = ast.literal_eval(new_repr)
    except (ValueError, SyntaxError):
        return None
    if not isinstance(old, str) or not isinstance(new, str):
        return None
    return ts, old, new


def self_test():
    assert parse_sel("", 3) == [0, 1, 2]
    assert parse_sel("1,3,7", 5) == [0, 2]
    assert parse_sel("1-3", 5) == [0, 1, 2]
    assert parse_sel("2-1", 5) == [0, 1]
    assert parse_sel("9,0,x,2", 5) == [1]
    assert parse_sel("1,1,2", 5) == [0, 1]
    assert parse_progress("[3/8]  19m – 29m  (25%)  [key #3]") == (
        "Transcribe chunk 3/8", 25)
    assert parse_progress("[Jepang → Indonesia] Batch 7/39  (49-56)  15%") == (
        "Translate batch 7/39", 15)
    assert parse_progress("SELESAI 323 segmen") == ("Selesai", 100)
    assert parse_progress("Pindah ke key #2...") == ("Rotasi key", -1)
    assert parse_progress("halo biasa") is None
    assert strip_ansi("\x1b[1;36mhalo\x1b[0m") == "halo"
    import tempfile
    d = tempfile.mkdtemp()
    open(os.path.join(d, "a.mp4"), "w").close()
    open(os.path.join(d, "b.txt"), "w").close()
    os.mkdir(os.path.join(d, "sub"))
    open(os.path.join(d, "sub", "c.mkv"), "w").close()
    assert expand_paths(["~/tak-ada-xyz"]) == []
    got = expand_paths([d, os.path.join(d, "*.mp4"), os.path.join(d, "b.txt")])
    assert got == [os.path.join(d, "a.mp4")], got
    assert expand_paths([d, d]) == sorted(
        [os.path.join(d, "a.mp4")])
    assert srt_status("/tak/ada/x_ID.srt") == (False, False, False)
    assert parse_proofread_line("00:02:07,680  'itu, Josh' → 'Ini Josh'") == (
        "00:02:07,680", "itu, Josh", "Ini Josh")
    assert parse_proofread_line('00:01:00,000  "don\'t" → \'jangan\'') == (
        "00:01:00,000", "don't", "jangan")
    assert parse_proofread_line("00:03:00,000  'x' → '(dibuang)'") == (
        "00:03:00,000", "x", "(dibuang)")
    assert parse_proofread_line("baris biasa") is None
    assert parse_proofread_line("00:01:00,000  noquote → 'x'") is None
    assert parse_progress("[proofread] chunk 3/31 [key #2]") == (
        "Proofread chunk 3/31", 9)
    assert is_progress_noise("│  Transcribe... -")
    assert is_progress_noise("│  Mux MKV... \\")
    assert is_progress_noise("frame= 500 fps=25 time=00:00:20 bitrate=100kbits/s")
    assert not is_progress_noise("[1/8]  0.0s – 10m  (0%)")
    assert not is_progress_noise("Batch 7/39  (49-56)  15%")
    assert not is_progress_noise("SELESAI 323 segmen")
    assert not is_progress_noise("x ERROR: boom")
    assert not is_progress_noise("ffmpeg error: Invalid data")
    print("tui self-test: semua OK")


# ── Runner subprocess (thread + queue) ───────────────────────────────────────
class Job:
    def __init__(self, cmd, env):
        self.cmd, self.env = cmd, env
        self.log = deque(maxlen=500)
        self.prog = ("Menunggu...", -1)
        self.rc, self.alive, self.proc = None, True, None

    def emit(self, raw):
        line = strip_ansi(raw).rstrip()
        if not line.strip():
            return
        p = parse_progress(line)
        if p:
            self.prog = p
        # ponytail: frame animasi CLI (spinner bash, progres \r ffmpeg) update
        # bar/status saja — jangan jadi baris log (banjir ratusan baris)
        if is_progress_noise(line):
            return
        if len(line) > 400:
            line = line[:400] + "…"
        self.log.append(line)

    def run(self):
        try:
            self.proc = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=ROOT, env=self.env)
            assert self.proc.stdout is not None
            buf = ""
            while True:
                ch = self.proc.stdout.read(1)
                if not ch:
                    break
                if ch in "\r\n":
                    if buf:
                        self.emit(buf)
                        buf = ""
                else:
                    buf += ch
                    if len(buf) > 2000:  # ponytail: tanpa newline (jarang) → flush paksa
                        self.emit(buf)
                        buf = ""
            if buf:
                self.emit(buf)
            self.rc = self.proc.wait()
        except Exception as e:  # ponytail: error spawn tampil di panel, bukan crash
            self.emit(f"x ERROR: {e}")
            self.rc = 127
        finally:
            self.alive = False

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        if self.proc and self.alive:
            try:
                self.proc.terminate()
            except Exception:
                pass


# ── Primitif UI ──────────────────────────────────────────────────────────────
_pending = deque(maxlen=16)  # ponytail: ketikan saat job jalan di-buffer,
                             # bukan dibuang — layar berikut konsumsi dulu


def next_key(stdscr):
    if _pending:
        return _pending.popleft()
    return get_key(stdscr)
def get_key(stdscr, timeout_ms=80):
    """getch + gabung escape-sequence panah manual.

    ponytail: keypad(True) saja tidak cukup — terminfo rusak/mismatch (kitty,
    ssh tanpa entry) bikin panah tiba sbg ESC,[,B terpisah dan ESC dikira
    batal. Wrapper ini mengenali sequence legacy umum tanpa peduli terminfo.
    """
    ch = stdscr.getch()
    if ch != 27:
        return ch
    stdscr.nodelay(True)
    try:
        seq, t0 = "", time.time()
        while len(seq) < 3 and (time.time() - t0) * 1000 < timeout_ms:
            c = stdscr.getch()
            if c == -1:
                time.sleep(0.005)
                continue
            if not 0 <= c < 256:
                break
            seq += chr(c)
            if seq in ("[A", "[B", "[C", "[D]", "OA", "OB", "OC", "OD",
                       "[5~", "[6~", "[H", "[F", "[3~"):
                break
        return {"[A": curses.KEY_UP, "[B": curses.KEY_DOWN,
                "[C": curses.KEY_RIGHT, "[D": curses.KEY_LEFT,
                "OA": curses.KEY_UP, "OB": curses.KEY_DOWN,
                "OC": curses.KEY_RIGHT, "OD": curses.KEY_LEFT,
                "[5~": curses.KEY_PPAGE, "[6~": curses.KEY_NPAGE,
                }.get(seq, 27)
    finally:
        stdscr.nodelay(False)
def draw_bar(win, y, x, w, pct):
    fill = max(0, min(w, w * pct // 100)) if pct >= 0 else 0
    win.addstr(y, x, "[" + "#" * fill + "-" * (w - fill) + "]")


def menu_pick(stdscr, title, items, footer="↑↓ pilih · Enter OK · q kembali"):
    """items: [(label, tag)]. → tag atau None."""
    cur = 0
    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        stdscr.addstr(0, 2, title[:w - 4], curses.A_BOLD)
        for i, (label, _tag) in enumerate(items):
            if 2 + i >= h - 2:
                break
            attr = curses.A_REVERSE if i == cur else 0
            stdscr.addstr(2 + i, 4, f" {label}"[:w - 6], attr)
        stdscr.addstr(h - 1, 2, footer[:w - 4], curses.A_DIM)
        stdscr.refresh()
        ch = next_key(stdscr)
        if ch in (ord("q"), 27):
            return None
        n = len(items)
        if ch == curses.KEY_UP:
            cur = (cur - 1) % n
        elif ch == curses.KEY_DOWN:
            cur = (cur + 1) % n
        elif ch in (curses.KEY_ENTER, 10, 13):
            return items[cur][1]
        elif ord("1") <= ch <= ord("9") and ch - ord("1") < n:
            return items[ch - ord("1")][1]


def multi_pick(stdscr, title, rows, footer="Space centang · a semua · Enter jalan · q batal",
               allow_empty=False):
    """rows: [(label, status)]. → [index], [] (kosong valid bila allow_empty),
    atau None (batal)."""
    cur, checked = 0, set()
    warn = ""
    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        stdscr.addstr(0, 2, f"{title} ({len(checked)} dipilih)"[:w - 4],
                      curses.A_BOLD)
        for i, (label, status) in enumerate(rows):
            if 2 + i >= h - 3:
                break
            mark = "[x]" if i in checked else "[ ]"
            attr = curses.A_REVERSE if i == cur else 0
            stdscr.addstr(2 + i, 4, f"{mark} {label} {status}"[:w - 6], attr)
        if warn:
            stdscr.addstr(h - 2, 4, warn[:w - 8], curses.A_BOLD)
        stdscr.addstr(h - 1, 2, footer[:w - 4], curses.A_DIM)
        stdscr.refresh()
        ch = next_key(stdscr)
        warn = ""
        if ch in (ord("q"), 27):
            return None
        n = len(rows)
        if not n:
            return []
        if ch == curses.KEY_UP:
            cur = (cur - 1) % n
        elif ch == curses.KEY_DOWN:
            cur = (cur + 1) % n
        elif ch == ord(" "):
            checked.discard(cur) if cur in checked else checked.add(cur)
        elif ch == ord("a"):
            checked = set() if len(checked) == n else set(range(n))
        elif ch in (curses.KEY_ENTER, 10, 13):
            if not checked and not allow_empty:
                warn = "! Centang dulu pakai Space (atau 'a' = semua)"
                continue
            return sorted(checked)


def text_rows(stdscr, title, prompt):
    """Input path satu-per-baris (Enter tambah, kosong = selesai). → [baris]/None."""
    rows, buf = [], ""
    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        stdscr.addstr(0, 2, title[:w - 4], curses.A_BOLD)
        for i, r in enumerate(rows[-(h - 5):]):
            stdscr.addstr(2 + i, 4, f"- {r}"[:w - 6])
        stdscr.addstr(h - 2, 2, f"{prompt}{buf}"[:(w - 4)])
        stdscr.addstr(h - 1, 2, "Enter tambah · kosong = selesai · q batal"[:w - 4],
                      curses.A_DIM)
        stdscr.refresh()
        ch = next_key(stdscr)
        if ch in (ord("q"), 27):
            return None
        if ch in (curses.KEY_ENTER, 10, 13):
            if not buf.strip():
                return rows
            rows.append(buf.strip())
            buf = ""
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
        elif 32 <= ch <= 0x10FFFF:
            try:
                buf += chr(ch)
            except ValueError:
                pass


def msg(stdscr, lines, footer="tombol apa saja = kembali"):
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    for i, ln in enumerate(lines[:h - 2]):
        stdscr.addstr(1 + i, 2, ln[:w - 4])
    stdscr.addstr(h - 1, 2, footer[:w - 4], curses.A_DIM)
    stdscr.refresh()
    next_key(stdscr)


def confirm(stdscr, question):
    h, w = stdscr.getmaxyx()
    q = question[:w - 6]
    stdscr.addstr(h - 3, 2, " " * (w - 4))
    stdscr.addstr(h - 3, 2, q + " [y/N]", curses.A_BOLD)
    stdscr.refresh()
    return next_key(stdscr) in (ord("y"), ord("Y"))


def run_screen(stdscr, title, job):
    """Streaming log + progress sampai job selesai. → rc (None = dihentikan)."""
    stdscr.nodelay(True)
    try:
        while job.alive:
            h, w = stdscr.getmaxyx()
            label, pct = job.prog
            stdscr.erase()
            stdscr.addstr(0, 2, title[:w - 4], curses.A_BOLD)
            if pct >= 0:
                draw_bar(stdscr, 2, 4, w - 16, pct)
                stdscr.addstr(2, w - 10, f"{pct:3d}%")
            else:
                # ponytail: spinner braille native (opsi B) — frame dari waktu,
                # redraw 10fps yang sudah ada, tanpa thread/timer baru
                frame = SPINNER[int(time.time() * 10) % len(SPINNER)]
                stdscr.addstr(2, 4, f"{frame} jalan")
            stdscr.addstr(3, 4, label[:w - 8])
            logs = list(job.log)[-(h - 7):]
            for i, ln in enumerate(logs):
                stdscr.addstr(5 + i, 2, ln[:w - 4])
            stdscr.addstr(h - 1, 2, "q = hentikan job"[:w - 4], curses.A_DIM)
            stdscr.refresh()
            c = stdscr.getch()
            if c == ord("q"):
                if confirm(stdscr, "Hentikan job ini?"):
                    job.stop()
                    while job.alive:
                        time.sleep(0.05)
                    return None
            elif c != -1:
                _pending.append(c)
            time.sleep(0.1)
        # ponytail: job kilat (dry-run/skip) selesai sebelum redraw ke-2 —
        # gambar sekali lagi biar status/log final selalu terlihat
        h, w = stdscr.getmaxyx()
        label, pct = job.prog
        stdscr.erase()
        stdscr.addstr(0, 2, title[:w - 4], curses.A_BOLD)
        if pct >= 0:
            draw_bar(stdscr, 2, 4, w - 16, pct)
            stdscr.addstr(2, w - 10, f"{pct:3d}%")
        for i, ln in enumerate(list(job.log)[-(h - 5):]):
            stdscr.addstr(4 + i, 2, ln[:w - 4])
        stdscr.refresh()
        return job.rc
    finally:
        stdscr.nodelay(False)


def _draw_diff(win, y, x, w, ts, old, new):
    """Satu baris diff: ts normal, old biru, → normal, new hijau (clip lebar)."""
    segs = [(f"{ts}  '", 0), (old, C_OLD), (f"' {ARROW} '", 0), (new, C_NEW),
            ("'", 0)]
    col = x
    for text, pair in segs:
        if col >= x + w:
            break
        text = text[:x + w - col]
        attr = curses.color_pair(pair) if HAS_COLOR and pair else 0
        try:
            win.addstr(y, col, text, attr)
        except curses.error:
            break
        col += len(text)


def view_log(stdscr, title, path, color_diff=False):
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines() or ["(kosong)"]
    except OSError as e:
        msg(stdscr, [f"x Tidak bisa baca {path}", str(e)])
        return
    parsed = [parse_proofread_line(ln) if color_diff else None for ln in lines]
    off = 0
    while True:
        h, w = stdscr.getmaxyx()
        off = max(0, min(off, max(0, len(lines) - (h - 2))))
        stdscr.erase()
        stdscr.addstr(0, 2, f"{title} ({len(lines)} baris)"[:w - 4],
                      curses.A_BOLD)
        for i, ln in enumerate(lines[off:off + h - 2]):
            diff = parsed[off + i]
            if diff and HAS_COLOR:
                _draw_diff(stdscr, 1 + i, 2, w - 4, *diff)
            else:
                stdscr.addstr(1 + i, 2, ln[:w - 4])
        stdscr.addstr(h - 1, 2, "↑↓/PgUp/PgDn scroll · q kembali"[:w - 4],
                      curses.A_DIM)
        stdscr.refresh()
        ch = next_key(stdscr)
        if ch in (ord("q"), 27):
            return
        if ch == curses.KEY_UP:
            off -= 1
        elif ch == curses.KEY_DOWN:
            off += 1
        elif ch == curses.KEY_PPAGE:
            off -= h - 3
        elif ch == curses.KEY_NPAGE:
            off += h - 3


# ── Alur ─────────────────────────────────────────────────────────────────────
def pick_lang(stdscr):
    tag = menu_pick(stdscr, "Bahasa sumber",
                    [(f"{c} — {l}", c) for c, l in LANGS])
    return tag  # None = batal


def pick_flags(stdscr):
    idx = multi_pick(stdscr, "Opsi jalan (boleh kosong = jalan normal)", [
        ("FORCE=1 — paksa ulang semua proses", ""),
        ("DRY_RUN=1 — preview tanpa panggil Groq", "")],
        allow_empty=True)
    if idx is None:
        return None
    return {"FORCE": "1" if 0 in idx else "0",
            "DRY_RUN": "1" if 1 in idx else "0"}


def base_env(lang, flags):
    env = dict(os.environ)
    env.update({"SUB_LANG": lang, "FORCE": flags["FORCE"],
                "DRY_RUN": flags["DRY_RUN"], "AUTO": "1",
                "MKV": "0", "CLEAN": "0", "TUI_QUIET": "1"})
    env.pop("TUI_MODE", None)
    env.pop("TUI_SRTS", None)
    return env


def _dur(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}m {sec % 60}s" if sec >= 60 else f"{sec}s"


def show_done(stdscr, title, video, i, n, rc, dt, job, allow_stop):
    """Layar selesai per video: status + waktu + ekor log, tombol SEGAR.

    ponytail: buffer ketikan lama dibuang di sini — keputusan Lanjut/Stop
    harus tombol baru, bukan sisa ketikan saat job jalan (itu yang bikin
    ringkasan ke-skip lalu loncat ke menu).
    """
    _pending.clear()
    ok = rc == 0
    mark = "✓ OK" if ok else f"x rc={rc}"
    lines = [f"{mark} — {title} [{i + 1}/{n}]",
             f"File  : {os.path.basename(video)}",
             f"Waktu : {_dur(dt)}",
             "Log akhir:"]
    lines += [f"  {ln}" for ln in list(job.log)[-8:]] or ["  (log kosong)"]
    while True:
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        for r, ln in enumerate(lines[:h - 2]):
            attr = curses.A_BOLD if r == 0 else 0
            stdscr.addstr(1 + r, 2, ln[:w - 4], attr)
        foot = ("[L]anjut  [S]top" if allow_stop
                else "tombol apa saja = lanjut")
        stdscr.addstr(h - 1, 2, foot[:w - 4], curses.A_DIM)
        stdscr.refresh()
        ch = next_key(stdscr)
        if allow_stop:
            if ch in (ord("s"), ord("S"), 27):
                return "stop"
            if ch in (ord("l"), ord("L"), 10, 13):
                return "go"
        else:
            return "go"


def run_videos(stdscr, title, videos, env, pause):
    """Satu invokasi sub.sh per video (pause antar-video ala sub.sh)."""
    results = []
    for i, v in enumerate(videos):
        t0 = time.time()
        job = _start([os.path.join(ROOT, "sub.sh"), v], env)
        rc = run_screen(stdscr, f"{title} [{i + 1}/{len(videos)}] "
                                f"{os.path.basename(v)}", job)
        dt = time.time() - t0
        results.append((v, rc))
        if rc is None:
            break
        if pause and i < len(videos) - 1:
            if show_done(stdscr, title, v, i, len(videos), rc, dt, job,
                         allow_stop=True) == "stop":
                return results
        else:
            show_done(stdscr, title, v, i, len(videos), rc, dt, job,
                      allow_stop=False)
    ok = sum(1 for _, rc in results if rc == 0)
    fail = sum(1 for _, rc in results if rc not in (0, None))
    lines = [f"Selesai: {ok} OK, {fail} gagal dari {len(results)} video."]
    for v, rc in results:
        mark = "OK" if rc == 0 else ("STOP" if rc is None else f"rc={rc}")
        lines.append(f"  [{mark}] {os.path.basename(v)}")
    msg(stdscr, lines)
    return results


def _start(cmd, env):
    job = Job(cmd, env)
    job.start()
    return job


def flow_pipeline(stdscr, mode):
    """Mode 1-4: pilih video → bahasa → flag → jalan (+pause kecuali mode 4)."""
    vids = list_videos()
    if not vids:
        msg(stdscr, ["x Tidak ada video di whisper/videos/",
                     "Taruh file dulu, atau pakai mode 7 (path lain)."])
        return
    rows = [(os.path.basename(v), f"{os.path.getsize(v) // 1048576} MB")
            for v in vids]
    idx = multi_pick(stdscr, "Pilih video", rows)
    if not idx:
        return
    lang = pick_lang(stdscr)
    if lang is None:
        return
    flags = pick_flags(stdscr)
    if flags is None:
        return
    env = base_env(lang, flags)
    if mode == "mkv":
        env["MKV"] = "1"
    elif mode == "clean":
        env.update({"MKV": "1", "CLEAN": "1"})
    titles = {"srt": "SRT saja", "mkv": "SRT + MKV", "clean": "SRT + MKV + Clean",
              "auto": "Auto SRT"}
    if mode == "clean" and not confirm(
            stdscr, "Mode 3 me-trash source setelah MKV sukses. Lanjut?"):
        return
    run_videos(stdscr, titles[mode], [vids[i] for i in idx], env,
               pause=(mode != "auto"))


def flow_custom(stdscr):
    lines = text_rows(stdscr, "Path lain (satu per baris)",
                      "path (~/x.mp4, glob, folder): ")
    if not lines:
        return
    vids = expand_paths(lines)
    if not vids:
        msg(stdscr, ["x Tidak ada path valid."])
        return
    lang = pick_lang(stdscr)
    if lang is None:
        return
    flags = pick_flags(stdscr)
    if flags is None:
        return
    run_videos(stdscr, "Path lain", vids, base_env(lang, flags), pause=False)


def flow_mux_scan(stdscr, kind):
    """Mode 5/6: pilih *_ID.srt → sub.sh TUI_MODE (reuse mux/proofread asli)."""
    srts = list_srts()
    if not srts:
        msg(stdscr, ["x Tidak ada *_ID.srt di srt/subtitles/"])
        return
    rows = []
    for s in srts:
        video, mkv, _log = srt_status(s)
        st = "✓ video ada" if video else "✗ video tidak ada"
        if mkv:
            st += " · MKV sudah ada"
        rows.append((os.path.basename(s), st))
    idx = multi_pick(stdscr,
                     "Pilih SRT" if kind == "mux" else "Pilih SRT (proofread)",
                     rows)
    if not idx:
        return
    if kind == "mux" and not confirm(
            stdscr, f"Mux {len(idx)} SRT ke MKV? (skip yg sudah ada)"):
        return
    env = dict(os.environ, AUTO="1", FORCE="0", DRY_RUN="0",
               TUI_QUIET="1", TUI_MODE=kind,
               TUI_SRTS="\n".join(srts[i] for i in idx))
    rc = run_screen(stdscr, "Mux MKV" if kind == "mux" else "Scan proofread",
                    _start([os.path.join(ROOT, "sub.sh")], env))
    if kind == "scan" and rc == 0:
        # ponytail: ringkasan + 'l' langsung buka log revisi file tadi (tanpa
        # picker lagi kalau cuma 1 file ber-log) — lebih cepat dari menu 8
        with_logs = [srts[i] for i in idx
                     if os.path.isfile(srts[i] + ".proofread.log")]
        lines = ["Proofread selesai (rc=0)."] + \
            [f"  - {os.path.basename(s)}" for s in with_logs]
        if with_logs:
            lines.append("Tekan l = log kata diganti (biru→hijau).")
        h, w = stdscr.getmaxyx()
        stdscr.erase()
        for i, ln in enumerate(lines[:h - 2]):
            stdscr.addstr(1 + i, 2, ln[:w - 4])
        stdscr.addstr(h - 1, 2, "l = log revisi · tombol lain = menu"[:w - 4],
                      curses.A_DIM)
        stdscr.refresh()
        if next_key(stdscr) in (ord("l"), ord("L")) and with_logs:
            if len(with_logs) == 1:
                s = with_logs[0]
                view_log(stdscr, os.path.basename(s), s + ".proofread.log",
                         color_diff=True)
            else:
                flow_viewer(stdscr)
        return
    msg(stdscr, [f"{'Mux' if kind == 'mux' else 'Proofread'} selesai (rc={rc})."] +
        ([f"  - {os.path.basename(srts[i])}" for i in idx] if rc == 0 else []))


def flow_viewer(stdscr):
    srts = [s for s in list_srts()
            if os.path.isfile(s + ".proofread.log")]
    if not srts:
        msg(stdscr, ["x Belum ada *.proofread.log (jalan mode 6 dulu)."])
        return
    tag = menu_pick(stdscr, "Log proofread",
                    [(os.path.basename(s), s) for s in srts])
    if tag:
        view_log(stdscr, os.path.basename(tag), tag + ".proofread.log",
                 color_diff=True)


def main(stdscr):
    curses.curs_set(0)
    try:
        curses.use_default_colors()  # ponytail: background ikut terminal
    except curses.error:             # (transparan tidak tertutup hitam opak)
        pass
    global HAS_COLOR
    try:
        # ponytail: pair 1 biru (sebelum) + 2 hijau (sesudah), bg -1 transparan
        if curses.has_colors():
            curses.start_color()
            curses.init_pair(C_OLD, curses.COLOR_BLUE, -1)
            curses.init_pair(C_NEW, curses.COLOR_GREEN, -1)
            HAS_COLOR = True
    except curses.error:
        HAS_COLOR = False
    stdscr.keypad(True)  # ponytail: tanpa ini panah = ESC+[+B terpisah (ESC dikira batal)
    h, w = stdscr.getmaxyx()
    if h < MIN_H or w < MIN_W:
        stdscr.erase()
        stdscr.addstr(0, 0, f"Terminal min {MIN_W}x{MIN_H} (kini {w}x{h}). "
                            "Perbesar lalu ulangi, atau pakai ./sub.sh.")
        stdscr.refresh()
        stdscr.getch()
        return
    items = [("1  Subtitle SRT saja", "srt"),
             ("2  SRT + Mux MKV", "mkv"),
             ("3  SRT + MKV + Clean source", "clean"),
             ("4  Auto SRT saja (tanpa pause)", "auto"),
             ("5  Mux MKV saja (pilih SRT)", "mux"),
             ("6  Scan konteks QA proofread", "scan"),
             ("7  Path lain (ketik manual)", "custom"),
             ("8  Lihat log proofread", "viewer"),
             ("9  Keluar", "quit")]
    while True:
        tag = menu_pick(stdscr, "catsub — pilih mode kerja", items)
        if tag in (None, "quit"):
            return
        if tag in ("srt", "mkv", "clean", "auto"):
            flow_pipeline(stdscr, tag)
        elif tag == "custom":
            flow_custom(stdscr)
        elif tag in ("mux", "scan"):
            flow_mux_scan(stdscr, tag)
        elif tag == "viewer":
            flow_viewer(stdscr)


FALLBACK = """tui.py butuh terminal interaktif (curses) min 80x24.
Setara sub.sh langsung:
  ./sub.sh                       # menu interaktif
  AUTO=1 SUB_LANG=ja ./sub.sh    # auto, bahasa Jepang
  MKV=1 AUTO=1 ./sub.sh v.mp4    # SRT + mux satu video
  TUI_MODE=mux TUI_SRTS=f_ID.srt ./sub.sh        # mux non-interaktif
  TUI_MODE=scan TUI_SRTS=f_ID.srt ./sub.sh       # proofread non-interaktif
"""

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    elif "--help" in sys.argv or "-h" in sys.argv:
        print((__doc__ or "") + FALLBACK)
    else:
        try:
            curses.wrapper(main)
        except Exception as e:  # ponytail: gagal init → perintah manual, bukan traceback
            print(f"x TUI gagal start ({e}).\n" + FALLBACK)
            sys.exit(2)
