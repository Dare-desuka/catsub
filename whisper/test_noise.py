# ponytail: self-check filter noise/halusinasi — jalankan: venv/bin/python3 test_noise.py
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import transcribe as t

CASES = [
    ("ぬはぁ", "ja", True), ("ぐはぁ", "ja", True), ("ぷのはぁ", "ja", True),
    ("ああぁ、 ああああ、", "ja", True), ("めめめめめめ、", "ja", True),
    ("ハァハァ", "ja", True), ("んぅあぁ", "ja", True),
    ("はい", "ja", False), ("うん", "ja", False),  # ucapan asli pendek dipertahankan
    ("ハァハァ、出しちゃう?", "ja", False),         # desah dalam kalimat natural
    ("ok", "en", False), ("ah", "en", True), ("um, um, um", "en", True),
]

fails = 0
for txt, lang, want in CASES:
    got = t.is_noise_segment(txt, lang)
    if got != want:
        print(f"FAIL {txt!r} ({lang}): want noise={want}, got {got}")
        fails += 1

h, why = t.is_hallucination("thank you jesus.", 1.0, "en")
if not h:
    print("FAIL halluc 'thank you jesus.':", why)
    fails += 1
h2, _ = t.is_hallucination("thank you for the meal.", 1.0, "en")
if h2:
    print("FAIL kalimat normal ikut ke-flag halusinasi")
    fails += 1

assert fails == 0, f"{fails} kasus gagal"
print(f"semua {len(CASES) + 2} kasus OK")
