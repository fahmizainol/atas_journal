"""Resample a wav down to something a browser will decode.

Written for one job: ATAS ships its spoken order cues at **384kHz** mono, which
is a container rate rather than content — measured energy above 20kHz in those
files is 0.0015% of the total, i.e. nothing. Browsers decline the rate anyway,
so the files have to come down to 44.1kHz before `frontend/public/sounds` can
serve them.

Band-limited resampling by windowed-sinc interpolation. No scipy in this venv,
and pulling one in for four short wavs would be the tail wagging the dog — the
kernel below is fifteen lines and does the same thing:

    output sample i  =  sum over nearby input samples of  x[j] * h(t_i - j)
    h(d)             =  2*fc * sinc(2*fc*d) * blackman(d / taps)

`fc` sits at 0.45 of the *output* Nyquist so the transition band lands between
19.8kHz and 22.05kHz — above anything in a recording of a voice, and low enough
that nothing folds back down into it.

Usage:
    python demo/resample_cue.py in.wav out.wav [--rate 44100]
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np

# How many input samples either side of each output sample the kernel spans.
# 64 gives ~6 zero crossings at this ratio: enough that the window, not the
# truncation, decides the stopband.
TAPS = 64


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Mono float32 in [-1, 1], plus the sample rate. Stereo is averaged down —
    every cue this is pointed at is a mono recording in a stereo container or
    already mono, and a spoken cue has no stereo image worth keeping."""
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path.name}: only 16-bit PCM is handled, got {w.getsampwidth() * 8}-bit")
        raw = w.readframes(w.getnframes())
        ch, sr = w.getnchannels(), w.getframerate()
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    ratio = sr_out / sr_in
    n_out = int(round(len(x) * ratio))
    # Where each output sample sits, measured in input samples.
    t = np.arange(n_out, dtype=np.float64) / ratio
    base = np.floor(t).astype(np.int64)
    # Cutoff in cycles per *input* sample. Upsampling needs no filter beyond the
    # input's own band, hence the min().
    fc = 0.45 * min(1.0, ratio)
    out = np.zeros(n_out, dtype=np.float64)
    for k in range(-TAPS, TAPS + 1):
        j = base + k
        d = t - j
        # Blackman over the kernel's support, so the tails are tapered rather
        # than chopped — a truncated sinc rings.
        u = np.clip((d + TAPS) / (2 * TAPS), 0.0, 1.0)
        win = 0.42 - 0.5 * np.cos(2 * np.pi * u) + 0.08 * np.cos(4 * np.pi * u)
        h = 2 * fc * np.sinc(2 * fc * d) * win
        # Edges clamp to the first/last sample rather than wrapping: these are
        # short recordings and wrapping would fold the end of a word onto its
        # start.
        out += x[np.clip(j, 0, len(x) - 1)] * h
    return out


def write_wav(path: Path, x: np.ndarray, sr: int, target_rms: float | None = None) -> None:
    if target_rms is not None:
        # Level-match to another set of recordings. Needed exactly once: ATAS has
        # no female "limit filled", so that one cue comes from the male voice —
        # and the two speakers were not cut at the same level (male RMS 0.29 vs
        # the female set's 0.22), which at one shared pack gain would make the
        # odd cue out the loudest thing the app says. RMS rather than peak,
        # because peak on continuous speech says nothing about how loud it reads.
        rms = float(np.sqrt((x**2).mean()))
        if rms > 0:
            x = x * (target_rms / rms)
    peak = float(np.abs(x).max())
    # The kernel's passband ripple can nudge a sample that was already at full
    # scale over the top. Pulled back only if that happened, so a file that had
    # headroom keeps the level it came with.
    if peak > 0.999:
        x = x * (0.999 / peak)
    pcm = np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--rate", type=int, default=44100)
    ap.add_argument(
        "--rms",
        type=float,
        default=None,
        help="scale the output to this RMS, to match a cue against another voice",
    )
    a = ap.parse_args()

    x, sr = read_wav(a.src)
    y = resample(x, sr, a.rate)
    write_wav(a.dst, y, a.rate, a.rms)
    y = read_wav(a.dst)[0]   # report what actually landed on disk
    rms = float(np.sqrt((y**2).mean()))
    print(
        f"{a.src.name} → {a.dst.name}: {sr}Hz→{a.rate}Hz  "
        f"{len(x)}→{len(y)} samples ({len(y) / a.rate:.2f}s)  "
        f"peak={np.abs(y).max():.3f} rms={rms:.4f}"
    )


if __name__ == "__main__":
    main()
