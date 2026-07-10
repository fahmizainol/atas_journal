"""Shared helpers for the direct-call tests.

The routers are exercised by calling their functions straight, with a temp DB
injected into ``deps._conn``. ``resolve_scope`` is a FastAPI dependency whose
defaults are ``Query(...)`` sentinels, so a direct call has to pass every
parameter — this wrapper does that once, and takes overrides by name.
"""

from __future__ import annotations

from api.scope import Scope, resolve_scope

_DEFAULTS: dict = {
    "view": "logical",
    "instruments": None,
    "accounts": None,
    "start": None,
    "end": None,
    "tags": None,
    "tz": None,
    "modes": None,
    "models": None,
    "include_archived": False,
}


def make_scope(**overrides) -> Scope:
    unknown = set(overrides) - set(_DEFAULTS)
    if unknown:
        raise TypeError(f"unknown resolve_scope params: {sorted(unknown)}")
    return resolve_scope(**{**_DEFAULTS, **overrides})
