"""YouTube trading stream -> timestamped transcript, ready for trade extraction.

Auto-captions arrive as rolling VTT: each cue repeats the tail of the previous
one, so a naive strip triples the word count and makes ctrl-F useless. This
collapses the rolling duplicates and re-emits ~15s blocks with a [HH:MM:SS]
prefix, because *video time is the only thing you can later map to a clock*.

Timestamps are the whole point. Do not strip them "to clean it up" — the
teardown workflow anchors video time to ET off things the presenter says out
loud, and without the prefixes that anchoring is impossible.

Usage:
    .venv/bin/python data/research/vwap-wave-livestreams/transcribe.py VIDEO_ID [...]

Writes data/research/vwap-wave-livestreams/<video_id>.txt and prints the
metadata line (title | duration | uploader | upload_date) you need to date the
session. NOTE: upload_date routinely lags the traded session by a day — confirm
the session date from the transcript body, never from the upload date alone.

Requires yt-dlp on PATH (it is: ~/.local/bin/yt-dlp). If it ever isn't,
`uvx yt-dlp ...` works without installing (pip here is PEP-668 blocked).
"""
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).parent
BLOCK_CHARS = 220  # ~15s of speech; small enough to locate, big enough to read


def fetch(video_id: str) -> Path:
    """Download auto-captions as VTT. Returns the .en.vtt path."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    meta = subprocess.run(
        ["yt-dlp", "--skip-download", "--print",
         "%(title)s | %(duration_string)s | %(uploader)s | %(upload_date)s", url],
        capture_output=True, text=True)
    print(f"{video_id}: {meta.stdout.strip()}")
    subprocess.run(
        ["yt-dlp", "--skip-download", "--write-auto-sub", "--sub-lang", "en",
         "--sub-format", "vtt", "-o", str(OUT / f"{video_id}.%(ext)s"), url],
        capture_output=True, text=True)
    return OUT / f"{video_id}.en.vtt"


def parse(vtt: Path) -> list[tuple[str, str]]:
    """(timestamp, text) cues with rolling-caption duplicates collapsed."""
    cues, ts = [], None
    for line in vtt.read_text(encoding="utf8").splitlines():
        if "-->" in line:
            ts = line.split(" --> ")[0].split(".")[0]
            continue
        if line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or not line.strip():
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if not (text and ts):
            continue
        # A rolling cue either contains the previous line or is contained by it;
        # keep the longer, which is the fully-typed-out version.
        if cues and (text in cues[-1][1] or cues[-1][1] in text):
            if len(text) > len(cues[-1][1]):
                cues[-1] = (cues[-1][0], text)
            continue
        cues.append((ts, text))
    return cues


def write_blocks(cues: list[tuple[str, str]], dest: Path) -> None:
    with dest.open("w", encoding="utf8") as fh:
        buf, start = [], None
        for ts, text in cues:
            start = start or ts
            buf.append(text)
            if len(" ".join(buf)) > BLOCK_CHARS:
                fh.write(f"[{start}] " + " ".join(buf) + "\n")
                buf, start = [], None
        if buf:
            fh.write(f"[{start}] " + " ".join(buf) + "\n")


def cue_grep(cues: list[tuple[str, str]], pattern: str, before: int = 2, after: int = 3):
    """Fine-grained context around trade language — block form is too coarse.

    Use this for the actual extraction pass: entries and exits are called in
    single short cues ("long at 268", "I'm out") that a 220-char block buries.
    """
    rx = re.compile(pattern, re.I)
    seen = -1
    for i, (ts, text) in enumerate(cues):
        if not rx.search(text):
            continue
        lo = max(0, i - before, seen)
        for j in range(lo, min(len(cues), i + after + 1)):
            print(cues[j][0], cues[j][1])
        seen = min(len(cues), i + after + 1)
        print("  ...")


TRADE_RX = (r"\b(long|short|scalp|cover(ed)?|peel|eject|took|take|grab|add|"
            r"reposition|flat|out|entry|fill|bought|buy|sold|sell|stop|target|"
            r"point[s]?|holding|held|runner|scratch|trail|starter)\b")

# Auto-captions mangle instrument tickers badly and always the same ways:
# "NQ" -> "and Q" / "ENQ" / "BenQ" / "N Q", and "ES" -> "Yes." mid-sentence
# ("I'm long here for IB low >> on Yes."). Screening on the clean ticker alone
# undercounts by a mile. The cache is NQ-only, so this is the screen that tells
# you whether phase 4 is even possible before you spend effort on it.
INSTRUMENT_RX = {
    "NQ":  r"(\bNQ\b|\bENQ\b|\bBenQ\b|\bN Q\b|\band Q\b|\bnasdaq\b)",
    "ES":  r"(\bES\b|\bE S\b|\bS&P\b|\bSPX\b|\bemini\b|\bYes\.)",
    "YM":  r"(\bYM\b|\bY M\b|\bdow\b)",
    "RTY": r"(\bRTY\b|\brussell\b)",
    "CL":  r"(\bCL\b|\bcrude\b|\bWTI\b)",
    "GC":  r"(\bGC\b|\bgold\b)",
    "NG":  r"(\bNG\b|\bnat[- ]?gas\b|\bnatural gas\b)",
}


def instrument_mix(cues: list[tuple[str, str]], window: int = 2) -> dict[str, int]:
    """Which products is the trade language actually attached to?

    Counts cues carrying TRADE_RX language, bucketed by whichever instrument is
    named within +/-`window` cues (a cue can count for more than one; cues that
    name none land in "?"). This is a coarse screen, not a trade count — its one
    job is to answer "does he trade NQ in this session, or is this an ES/YM day
    we can never verify?" before phase 4 is promised to anyone.
    """
    trade = re.compile(TRADE_RX, re.I)
    rxs = {k: re.compile(v, re.I) for k, v in INSTRUMENT_RX.items()}
    counts = {k: 0 for k in INSTRUMENT_RX} | {"?": 0}
    for i, (_, text) in enumerate(cues):
        if not trade.search(text):
            continue
        ctx = " ".join(t for _, t in cues[max(0, i - window):i + window + 1])
        hit = [k for k, rx in rxs.items() if rx.search(ctx)]
        for k in hit or ["?"]:
            counts[k] += 1
    return counts


if __name__ == "__main__":
    for vid in sys.argv[1:]:
        cues = parse(fetch(vid))
        write_blocks(cues, OUT / f"{vid}.txt")
        mix = {k: v for k, v in instrument_mix(cues).items() if v}
        print(f"  -> {vid}.txt  ({len(cues)} cues)")
        print(f"     instrument mix (trade-language cues): {mix}")
