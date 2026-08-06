"""Live shadow mode — watch a session in progress and report where the
registered strategies *would have* signalled.

No order routing, ever. The app watches and reports; prop-firm rules diverge
exactly on that line, and shadow mode is deliberately on the safe side of it.
See docs/live-shadow-plan.md for the decisions this package is built on; the two
that shape the code most:

  - **The engine is the only source of truth.** A signal is a prefix re-run of
    the same ``run_session`` the backtest calls (``journal.sim.live_shadow``), so
    live cannot disagree with the backtest about a fill. A cheaper incremental
    ``step()`` would be a second implementation of the entry rules, which this
    repo has already ruled twice against.
  - **What writes, and what does not.** The fake feed records nothing: its
    source is a cached Databento day already on disk, so persisting it would
    write a second copy of a file we have — and would manufacture a "live" day
    out of a replayed one. The Rithmic feed records every print, because ten
    ``gx_*`` gate sites and the weekly seed read a session's earlier windows off
    disk and blind-fail-closed when they are not there; a memory-only live day
    is not a reduced-fidelity shadow mode, it is one where seven strategies
    never signal and nothing says why.

Live ticks and the Databento corpus stay permanently disjoint stores, and every
reader resolves Databento first. That is what makes "do live signals match the
backtest" a question with an answer: recording a session can never change what a
backtest over that session says.
"""

from .session import LiveSession
from .state import check_modes, current, resume, set_modes, start, start_rithmic, stop

__all__ = ["LiveSession", "check_modes", "current", "resume", "set_modes",
           "start", "start_rithmic", "stop"]
