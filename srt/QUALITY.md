# CatSub subtitle quality audit

The translator's format checks ensure that each API batch returns the expected cue IDs. This standalone, read-only audit adds a final check of the generated SRT's structure and common quality risks. It does not rewrite files or claim to judge translation accuracy.

Run after translating a Japanese source SRT:

```bash
python3 srt/quality_check.py srt/subtitles/video_ID.srt --lang ja --source whisper/transcripts/video.srt
```

Supported source-script checks: `ja`, `zh`, `ko`, `th`, and `ru`. The `--source` option flags longer cues that are identical to the corresponding source cue; this is only a warning because names and short expressions may legitimately remain unchanged.

The audit treats malformed SRT, empty output, and unresolved `[BELUM_DITERJEMAHKAN]` / model-instruction text as errors. It reports likely leftover source characters, overlapping timings, long cues, and high reading speed as warnings. Use `--strict` in a review workflow to make warnings fail the command too. Warnings require human review; they are not proof that a translation is wrong.

Run unit tests with:

```bash
cd srt && python3 -m unittest test_quality_check.py
```
