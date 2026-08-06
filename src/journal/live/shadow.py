"""Where the registered strategies *would have* signalled, on the day so far.

Every strategy on the shelf is re-run over the live prefix on a cadence, through
``journal.sim.live_shadow`` — which is the same ``run_session`` the backtest
calls, with ``partial=True``. Not an incremental ``step()``: a second
implementation of the entry rules could disagree with the backtest about a fill,
and these strategies are marginal enough (weekly-traverse PF 1.10; one run's top
20 trades were 101% of net) that one bar's difference flips the sign of an edge.
``tests/test_prefix_replay.py`` is what keeps the prefix property true.

THE CONFIG IS THE BASELINE'S. A live signal is only interesting next to the
backtest that motivated it, so each strategy runs the config of its own pinned
baseline run — not the registry default, and not something typed in here. A
strategy with no baseline pinned is skipped and says so, rather than being run
under a config nobody validated.

THE CONTRACT IS REPINNED. ``cfg.contract`` is overwritten with the live raw
symbol before every run. A baseline's config usually carries the rolling root
("NQ"), and ``contract_for`` resolves a root by probing Databento — a live path
must never do that, and the on-disk roll map ends 2026-06-30 anyway. A raw
contract resolves to itself, offline, in ``contract_for_cached``.

CADENCE. Strategy state changes on bar closes, so re-running per tick only burns
CPU. Each strategy re-runs once ``ticks_per_bar`` prints have arrived since its
last run, and never more often than ``CADENCE_FLOOR_S``. At real NQ rates that is
roughly every half-minute per strategy; the floor is what stops a fast-forwarded
fake feed from running the whole shelf flat out. Measured: all thirteen
strategies over a whole cached session cost ~4.6s wall in total.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from dataclasses import dataclass, field

import pandas as pd

from ..config import ET_TZ
from ..sim import live_shadow, registry, store
from ..sim import regime as regmod
from .session import LiveSession

# Never re-run a strategy more often than this, however fast ticks arrive.
CADENCE_FLOOR_S = 5.0
# How often the runner wakes to see whether anything is due.
_WAKE_S = 1.0


@dataclass
class Watch:
    """One strategy being shadowed, and what it last said."""

    slug: str
    name: str
    version: str
    session: str  # "rth" | "globex" — which window its frame is cut to
    run_id: str  # the baseline whose config this is running
    cfg: object
    trades: list[dict] = field(default_factory=list)
    vetoed: list[dict] = field(default_factory=list)
    # Cadence state: rows on the tape and monotonic seconds at the last run.
    last_rows: int = 0
    last_at: float = 0.0
    ran: bool = False
    error: str | None = None


def resolve_watches(symbol: str) -> tuple[list[Watch], list[dict]]:
    """Bind every registered strategy to its pinned baseline config.

    Module-level rather than a method because the reconciliation (Phase 6) has to
    re-run the *same shelf under the same configs* after the close, and a second
    resolution that drifted from this one would show up as signal disagreement
    that was really configuration disagreement — the exact confusion the
    ordering of Phase 6's three comparisons exists to prevent.

    Returns (watches, skipped). A strategy with no baseline is skipped and says
    so: shadow mode runs each idea under the config its own baseline validated,
    and there is nothing honest to run one under otherwise.
    """
    watches: list[Watch] = []
    skipped: list[dict] = []
    for slug, strat in registry.STRATEGIES.items():
        rid = store.baseline(slug)
        if not rid:
            skipped.append({"slug": slug, "reason": "no baseline pinned"})
            continue
        run = store.read_run(slug, rid)
        if run is None:
            skipped.append({"slug": slug, "reason": f"baseline {rid} unreadable"})
            continue
        cfg = store.config_from_json(run[0], strat.config_cls)
        # Repin to the raw contract actually trading — see the module note.
        cfg = dataclasses.replace(cfg, contract=symbol)
        watches.append(Watch(slug=slug, name=strat.name, version=strat.version,
                             session=strat.session, run_id=rid, cfg=cfg))
    return watches, skipped


class ShadowRunner:
    """Re-runs the shelf over a live session on a cadence, on its own thread."""

    def __init__(self, session: LiveSession, journal=None,
                 enabled: bool = True) -> None:
        self.session = session
        # Where each pass's answer is written down, or None when nothing is
        # being recorded. Phase 6's prefix-integrity check compares what the
        # runner said *during* the session against one full run over the same
        # tape afterwards, and only the first half of that has to be captured
        # while it happens. A fake feed gets no journal for the same reason it
        # gets no recorder — see `journal.live.state`.
        self.journal = journal
        # Whether the shelf is being run at all. A disabled runner is built and
        # resolved but never started, and `snapshot` says `enabled: false` —
        # which is a different statement from "nothing has signalled", and the
        # surface has to be able to tell them apart. Toggled at runtime through
        # `state.set_modes`; the guard that decides when turning it *on* is
        # honest lives there, because it is a question about what is on disk.
        self.enabled = enabled
        self.watches: list[Watch] = []
        self.skipped: list[dict] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # The frozen regime: checkpoint name -> the KPIs as computed the first
        # time that checkpoint's cutoff had passed. See `_live_regime`.
        self._frozen: dict[str, dict] = {}
        # The newest frozen checkpoint, for display. Not what any gate reads.
        self._headline: dict | None = None
        self._resolve()

    def _resolve(self) -> None:
        self.watches, self.skipped = resolve_watches(self.session.symbol)

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        # Cleared rather than assumed unset: a runner that was stopped and is
        # being started again holds a set event, and a thread that reads it on
        # its first pass would exit before running anything — silently, and
        # looking exactly like a market with no setups in it.
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="live-shadow", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=5.0)
        self._thread = None

    def set_enabled(self, on: bool) -> None:
        """Turn the shelf on or off under a running session.

        Off leaves the watches resolved and the last answers standing — what
        stops is the re-running, so the panel keeps showing what the shelf said
        up to the moment it was switched off rather than blanking. `enabled`
        travels on the snapshot so that is readable as a state and not mistaken
        for a stalled runner.
        """
        if bool(on) == self.enabled:
            return
        self.enabled = bool(on)
        if self.enabled:
            self.start()
        else:
            self.stop()

    def set_journal(self, journal) -> None:
        """Swap where passes are written down (or stop writing them).

        Read by ``run_due`` outside the lock, so a pass already in flight may
        write one more line to the old journal or one fewer to the new one. That
        is deliberate: taking the lock across a file append would put every
        ``/live/signals`` request behind it, and the prefix check consumes
        journals as "what was said", which a boundary line is either way.
        """
        self.journal = journal

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_due()
            except Exception as e:  # noqa: BLE001 — the loop outlives one bad pass
                print(f"[live-shadow] pass failed: {type(e).__name__}: {e}", flush=True)
            self._stop.wait(_WAKE_S)

    # --- the regime artifact ------------------------------------------------

    def _live_regime(self, on: pd.DataFrame | None, rth: pd.DataFrame | None) -> dict:
        """The session's regime as it stands — checkpoint by checkpoint, frozen.

        A checkpoint is computed the first time its ET cutoff has passed and then
        never recomputed. Checkpoints are already causal in ``compute_regime``
        (each slices to the bars that had closed by its own cutoff), so freezing
        does not change any value; what it buys is that it *cannot*, which is the
        property worth having when a gate's verdict is downstream of it.

        Checkpoints whose cutoff has NOT passed are deliberately absent. Present
        but computed over a short prefix would be a fabricated verdict — the
        "12:00" KPIs at half past nine are just the morning wearing noon's label.
        Absent, a regime gate blind-fails-closed, and since every such gate only
        applies its veto from its own checkpoint minute onwards, it is inert until
        the moment it can honestly answer.

        THIS ALWAYS RETURNS A DICT, NEVER None. ``gates._regime_art`` falls back
        to ``get_regime`` (a cached read of the *whole settled day*) when the
        injected artifact is None. On a fake feed whose source is a cached day
        that file exists, so returning None here would hand every gate the
        finished day's answer at nine in the morning — lookahead, silent, and
        flattering. An artifact with no checkpoints is the honest empty answer.
        """
        last = self.session.last_ts()
        art_base = {
            "version": regmod.REGIME_VERSION,
            "symbol": self.session.symbol,
            "date": self.session.day.isoformat(),
            "live": True,
            "partial": on is None or on.empty,
        }
        if last is None:
            return {**art_base, "checkpoints": {}, "class": None, "texture": None,
                    "ribbon": []}
        et = last.tz_convert(ET_TZ)
        due = [name for name, t in regmod.CHECKPOINTS
               if (et.hour, et.minute) >= (t.hour, t.minute)]
        fresh = [c for c in due if c not in self._frozen]
        if fresh:
            art = regmod.compute_regime(self.session.symbol, self.session.day,
                                        frames=(on, rth))
            if art is not None:
                for c in fresh:
                    if c in art["checkpoints"]:
                        self._frozen[c] = art["checkpoints"][c]
        # The headline label is the newest checkpoint that has actually been
        # reached, not `eod` — which on a session in progress is only ever "the
        # day so far" wearing the closing bell's name. No gate reads it; the UI
        # does, and it should read the truth.
        newest = None
        for name, _ in regmod.CHECKPOINTS:
            if name in self._frozen:
                newest = self._frozen[name]
        self._headline = newest
        return {
            **art_base,
            "checkpoints": dict(self._frozen),
            "class": (newest or {}).get("class"),
            "texture": (newest or {}).get("texture"),
            "ribbon": [],
        }

    # --- the pass -----------------------------------------------------------

    def run_due(self) -> None:
        """Re-run every strategy whose cadence has come round."""
        if not self.enabled:
            return
        rows = self.session.n
        if rows == 0:
            return
        now = time.monotonic()
        due = [w for w in self.watches
               if (not w.ran and now - w.last_at >= CADENCE_FLOOR_S)
               or (rows - w.last_rows >= w.cfg.ticks_per_bar
                   and now - w.last_at >= CADENCE_FLOOR_S)]
        if not due:
            return

        # Cut each window once and share it. Materialising the day-so-far is the
        # cheap half of a pass, but doing it thirteen times for two distinct
        # answers is still thirteen copies of a million rows.
        rth = self.session.frame_for(overnight=False)
        gx = (self.session.frame_for(overnight=True)
              if any(w.session == "globex" for w in due) else None)
        regime = self._live_regime(self.session.overnight_frame(),
                                   rth if not rth.empty else None)

        for w in due:
            frame = gx if w.session == "globex" else rth
            try:
                trades, vetoed, _bars, _bands = live_shadow.shadow_session(
                    w.slug, w.cfg, self.session.day, frame, regime=regime)
                err = None
            except Exception as e:  # noqa: BLE001 — one strategy's failure is its own
                trades, vetoed, err = [], [], f"{type(e).__name__}: {e}"
            with self._lock:
                w.trades, w.vetoed, w.error = trades, vetoed, err
                w.last_rows, w.last_at, w.ran = rows, time.monotonic(), True
            if self.journal is not None:
                # Outside the lock: this is a file append, and the snapshot
                # endpoint should not be waiting behind it.
                self.journal.record(w.slug, rows, self.session.last_ts(),
                                    trades, vetoed, err)

    # --- reading ------------------------------------------------------------

    def snapshot(self) -> dict:
        """Everything the live surface shows about signals, as of now."""
        with self._lock:
            watches = [{
                "slug": w.slug,
                "name": w.name,
                "version": w.version,
                "session": w.session,
                "baseline_run_id": w.run_id,
                "ran": w.ran,
                "rows_at_last_run": w.last_rows,
                "error": w.error,
                "trades": w.trades,
                "vetoed": w.vetoed,
            } for w in self.watches]
        return {
            "gen": self.session.gen,
            "rows": self.session.n,
            # Both said out loud, because "no signals" has three causes and only
            # one of them is the market: the shelf is off, the shelf is on but
            # nothing has fired, or the shelf is on and firing but nothing is
            # writing it down for the reconciliation.
            "enabled": self.enabled,
            "journalling": self.journal is not None,
            "strategies": watches,
            "skipped": self.skipped,
            "regime": {
                # In CHECKPOINTS order, so the list reads as the day's progress.
                "frozen": [c for c, _ in regmod.CHECKPOINTS if c in self._frozen],
                "class": (self._headline or {}).get("class"),
                "texture": (self._headline or {}).get("texture"),
            },
        }
