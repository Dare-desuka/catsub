# ponytail: self-check filter noise/halusinasi — jalankan: venv/bin/python3 test_noise.py
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transcribe as t
import groq_util as gu

CASES = [
    ("ぬはぁ", "ja", True), ("ぐはぁ", "ja", True), ("ぷのはぁ", "ja", True),
    ("ああぁ、 ああああ、", "ja", True), ("めめめめめめ、", "ja", True),
    ("ハァハァ", "ja", True), ("んぅあぁ", "ja", True),
    ("きゅ、 きゅ、", "ja", True), ("ぬふぅ", "ja", True), ("ぐん、 ぐん", "ja", True),
    ("はい", "ja", False), ("うん", "ja", False),  # ucapan asli pendek dipertahankan
    ("アア", "ja", True), ("けんふ", "ja", True), ("んくるっん", "ja", True),  # MIUM-1437
    ("わあ", "ja", False), ("ほう", "ja", False),  # interjeksi legit, jangan dibuang
    ("嫌だ", "ja", False), ("やだ", "ja", False),  # kata asli ("don't"), walau sering jadi render erangan
    ("ハァハァ、出しちゃう?", "ja", False),         # desah dalam kalimat natural
    ("ok", "en", False), ("ah", "en", True), ("um, um, um", "en", True),
]

FILLER_REP_CASES = [
    ("うん、うん、うん、うん、ゆっくりして", "うん"),      # filler 2char ×4 → FLAG
    ("um, um, um, um, hello", "um"),                        # EN filler 2char ×4 → FLAG
    ("um um um um", "um"),                                  # 4x tanpa koma → FLAG
    ("うん、うん、うん、ゆっくりして", ""),                    # 3x < threshold → lolos
    ("panas banget, panas banget, panas banget", ""),        # kata panjang, dialogue asli → lolos
    ("hello", ""),                                           # single word → lolos
    ("はい、うん、ありがとう", ""),                            # berbeda kata → lolos
    ("暑いな、暑いな、暑いな、暑いですよ", ""),                  # 3char ×3, dialogue → lolos
    # Fix C: concatenated + char-run
    ("いくいくいくいくいくいくいくいく", "いく"),                # concatenated: return shortest unit
    ("ううううううううう", "うう"),                             # char-run ≥6x hiragana → FLAG
    ("クククククククク", "クク"),                               # char-run katakana ≥6x → FLAG
]

fails = 0
for txt, lang, want in CASES:
    got = t.is_noise_segment(txt, lang)
    if got != want:
        print(f"FAIL is_noise_segment {txt!r} ({lang}): want noise={want}, got {got}")
        fails += 1

for txt, want in FILLER_REP_CASES:
    got = gu.has_filler_repetition(txt)
    if got != want:
        print(f"FAIL has_filler_repetition {txt!r}: want {want!r}, got {got!r}")
        fails += 1

h, why = t.is_hallucination("thank you jesus.", 1.0, "en")
if not h:
    print("FAIL halluc 'thank you jesus.':", why)
    fails += 1
h2, _ = t.is_hallucination("thank you for the meal.", 1.0, "en")
if h2:
    print("FAIL kalimat normal ikut ke-flag halusinasi")
    fails += 1
h3, _ = t.is_hallucination("とものうち、 のまんこに、 きのこ、 ちん、", 4.0, "ja")
if not h3:
    print("FAIL halluc 'とものうち':", _)
    fails += 1

# ponytail: self-check is_low_confidence — nsp>0.85 saja cukup (SONE-970:
# BGM nsp=0.87/alp=-0.26 lolos AND lama; dialog asli nsp=0.16 lolos).
LOWCONF_CASES = [
    ({"no_speech_prob": 0.868, "avg_logprob": -0.26, "compression_ratio": 0.7}, True),
    ({"no_speech_prob": 0.157, "avg_logprob": -0.52, "compression_ratio": 1.1}, False),
    ({"no_speech_prob": 0.70, "avg_logprob": -1.50, "compression_ratio": 1.0}, True),
    ({"no_speech_prob": 0.90}, True),   # field lain absen → tetap buang
    ({}, False),                        # semua absen → jangan buang buta
]
for blk, want in LOWCONF_CASES:
    got, _ = t.is_low_confidence(blk)
    if got != want:
        print(f"FAIL is_low_confidence {blk}: want {want}, got {got}")
        fails += 1

# ponytail: self-check snap_to_speech — start longgar dijepit ke onset, ujung
# tidak ketarik ke ucapan tetangga, fallback & guard min-dur jalan.
_speech = [(1.0, 3.0), (5.0, 7.0)]
SNAP_CASES = [
    # (block, speech, sync_shift, want_start, want_end)
    ({"start": 0.5, "end": 3.5, "text": "x"}, _speech, 0.0, 0.9, 3.15),
    ({"start": 0.5, "end": 7.5, "text": "x"}, _speech, 0.0, 0.9, 7.15),  # rentang 2 ucapan
    ({"start": 6.0, "end": 8.0, "text": "x"}, _speech, 0.0, 6.0, 7.15),  # onset di kiri batas = milik tetangga → start utuh
    ({"start": 7.08, "end": 15.18, "text": "x"}, [(5.35, 7.66), (8.68, 11.07)], 0.0, 8.58, 11.22),  # kasus nyata docker
    ({"start": 10.0, "end": 12.0, "text": "x"}, _speech, 0.0, 10.0, 12.0),  # jauh dari speech → utuh
    ({"start": 0.5, "end": 3.5, "text": "x"}, [], 0.0, 0.8, 3.35),  # VAD gagal → fallback trim
    ({"start": 0.5, "end": 3.5, "text": "x"}, _speech, 0.5, 1.4, 3.65),  # sync-shift +
    ({"start": 5.0, "end": 5.1, "text": "x"}, _speech, 0.0, 4.9, 7.15),  # di dalam ucapan → ikut batas ucapan
    ({"start": 5.0, "end": 5.1, "text": "x"}, [], 0.0, 5.3, 5.6),  # guard min-dur 0.3
]
for blk, sp, shift, ws, we in SNAP_CASES:
    t.SYNC_SHIFT = shift
    got = t.snap_to_speech([dict(blk)], sp)[0]
    if abs(got["start"] - ws) > 1e-6 or abs(got["end"] - we) > 1e-6:
        print(f"FAIL snap {blk} shift={shift}: want ({ws},{we}), got ({got['start']},{got['end']})")
        fails += 1
t.SYNC_SHIFT = 0.0

# ponytail: self-check split_on_silence — 1 gap, tanpa gap, multi-gap, fallback
# proporsional, gap di tepi, speech kosong.
_g1 = [(0.0, 4.0), (5.5, 10.0)]
_r = t.split_on_silence([{"start": 0.0, "end": 10.0, "text": "Kalimat satu. Kalimat dua."}], _g1)
assert len(_r) == 2 and _r[0]["text"] == "Kalimat satu." and _r[1]["text"] == "Kalimat dua.", _r
assert abs(_r[0]["end"] - 4.75) < 1e-6 and abs(_r[1]["start"] - 4.75) < 1e-6, _r
_r = t.split_on_silence([{"start": 0.0, "end": 10.0, "text": "A. B."}], [(0.0, 4.0), (4.5, 10.0)])
assert len(_r) == 1, _r  # gap 0.5 < 1.5 → utuh
_r = t.split_on_silence([{"start": 0.0, "end": 12.0, "text": "Satu. Dua. Tiga."}],
                         [(0.0, 2.0), (4.0, 6.0), (8.0, 12.0)])
assert [p["text"] for p in _r] == ["Satu.", "Dua.", "Tiga."], _r
_r = t.split_on_silence([{"start": 0.0, "end": 10.0, "text": "abcdefghij"}], _g1)
assert [_r[0]["text"], _r[1]["text"]] == ["abcd", "efghij"], _r  # tanpa tanda baca → proporsional
_r = t.split_on_silence([{"start": 0.0, "end": 9.5, "text": "A. B."}], [(0.0, 8.0), (9.6, 12.0)])
assert len(_r) == 1, _r  # gap nyentuh tepi → skip
_r = t.split_on_silence([{"start": 0.0, "end": 10.0, "text": "A. B."}], [])
assert len(_r) == 1, _r
print("split_on_silence: 6 kasus OK")

# ponytail: check parser SRT lokal — timestamp → detik, teks multiline utuh.
import tempfile
from pathlib import Path as _P
with tempfile.NamedTemporaryFile("w", suffix=".srt", delete=False, encoding="utf-8") as _f:
    _f.write("1\n00:00:01,500 --> 00:00:03,000\nHalo\n\n2\n00:01:10,000 --> 00:01:12,250\nbaris satu\nbaris dua\n")
_pblocks = t.parse_srt_file(_P(_f.name))
assert len(_pblocks) == 2, _pblocks
assert abs(_pblocks[0]["start"] - 1.5) < 1e-6 and abs(_pblocks[0]["end"] - 3.0) < 1e-6
assert _pblocks[1]["text"] == "baris satu\nbaris dua" and abs(_pblocks[1]["start"] - 70.0) < 1e-6
_P(_f.name).unlink()

assert fails == 0, f"{fails} kasus gagal"
print(f"semua {len(CASES) + len(FILLER_REP_CASES) + 3 + len(SNAP_CASES) + len(LOWCONF_CASES)} kasus OK")
