# Order cues

The sounds the Charts pages make when an order goes out and when it comes back
filled. Played by `src/lib/orderSound.ts`, which falls back to a pair of
synthesised tones if a file fails to load.

Two packs, switched from the 🔊 button on the chart top bar (off → tones →
spoken → off, each press playing what it landed on):

| cue | tones | spoken (`voice/`) |
| --- | --- | --- |
| order placed | `order-placed.wav` (QT `OrderCreated`) | `order-placed.wav` |
| order filled | `order-filled.wav` (QT `OrderFilled`) | `order-filled.wav` |
| limit filled | — *(borrows the fill sound)* | `limit-filled.wav` |
| stop filled | — *(falls back to a tone)* | `stop-filled.wav` |
| order canceled | — | `order-canceled.wav` |
| order changed | — | `order-changed.wav` |
| connection lost | — | `connection-lost.wav` |
| win / loss on a close | tones | tones |

Quantower recorded two of these and ATAS recorded seven, so the tones pack
covers what it has and the rest fall back to synthesised cues — which is the
right way round: the events Quantower has no word for are the ones a tone says
perfectly well.

Every spoken file is ATAS's **female** voice except `limit-filled.wav`, which is
the **male2** voice — ATAS never recorded that cue for her. The two speakers were
not cut at the same level (male RMS 0.29 against the female set's 0.22), so that
one file was pulled to the female set's mean RMS at conversion time; at one
shared pack gain it would otherwise be the loudest thing the app says.

No pack has a word for "that close paid" — no platform does, because none of them
decides it for you — so a win or an ordinary loss stays a tone in both packs. A
stop-out is the exception: ATAS names it, and it is the exit nobody chose.

Taken from the platforms installed on this machine rather than synthesised,
because these are the cues a futures trader already reacts to without thinking —
which is the whole point of a sound: one you have to interpret sends you back to
the screen you were trying not to look at.

## The resampling

ATAS ships its spoken cues at **384kHz mono**, which no browser will decode.
They were brought down to 44.1kHz with:

```
python demo/resample_cue.py "/mnt/c/Program Files/ATAS X/Sounds/female_order placed.wav" \
    frontend/public/sounds/voice/order-placed.wav

# the one from the other voice, level-matched to the female set's mean RMS
python demo/resample_cue.py "/mnt/c/Program Files/ATAS X/Sounds/Voice/male2_limit filled.wav" \
    frontend/public/sounds/voice/limit-filled.wav --rms 0.2175
```

Nothing was lost doing it: measured energy above 20kHz in the originals is
0.0015% of the total — the rate was a container, not content — and comparing
band energies before and after puts every band within 0.002 percentage points.
Level is untouched (peak 0.92, RMS 0.21 in both), so the pack's playback gain in
`orderSound.ts` is what holds the two packs to the same loudness.

The Quantower files needed no conversion (44.1kHz 16-bit stereo already) and are
byte-identical to the installed ones, trailing silence and all.

## Licensing

These are two commercial platforms' assets, kept here for this local, personal
tool. They are not ours to redistribute — if this frontend is ever published
somewhere public, swap them for something licensed, or just delete them: every
cue falls back to a synthesised tone and nothing else has to change.

What is left unused: ATAS's Russian voice (`female ru_*`, the only complete set —
it has both the cues each English voice is missing), the male voice's other six
recordings, and its short beeps (`bip`, `tap`, `beep_*`). Quantower has
`PositionClosed.wav` and `OrderRejected.wav`. Nothing in the app plays a
rejection today — a refused order surfaces as text on the page instead.
