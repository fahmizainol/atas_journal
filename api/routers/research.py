"""Research notes: ``docs/research/*.md`` and ``*.html`` served read-only into the Lab.

The files are the primary artifact — written in the repo, versioned in git,
readable by an LLM without the app running. Markdown docs are rendered by the
frontend; HTML docs (Claude artifact pages saved from past study sessions) are
self-contained pages and are served verbatim into a sandboxed iframe. The API
only lists and serves them; writing happens in an editor, never through this
router.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

RESEARCH_DIR = Path(__file__).resolve().parents[2] / "docs" / "research"

router = APIRouter()


def _md_title(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _html_title(text: str) -> str:
    m = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _date(text: str) -> str | None:
    # The markdown docs open with a "- **Date:** 2026-07-19" metadata bullet.
    m = re.search(r"\*\*Date:\*\*\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text)
    return m.group(1) if m else None


def _find(slug: str) -> Path | None:
    # Resolve against the directory listing rather than joining the slug into
    # a path, so traversal sequences can never name a file outside it.
    if RESEARCH_DIR.is_dir():
        for p in RESEARCH_DIR.iterdir():
            if p.suffix in (".md", ".html") and p.stem == slug:
                return p
    return None


@router.get("/research")
def list_docs() -> list[dict]:
    if not RESEARCH_DIR.is_dir():
        return []
    docs = []
    for p in sorted(RESEARCH_DIR.iterdir()):
        if p.suffix not in (".md", ".html"):
            continue
        text = p.read_text(encoding="utf-8")
        kind = "md" if p.suffix == ".md" else "html"
        title = (_md_title(text) if kind == "md" else _html_title(text)) or p.stem
        docs.append(
            {
                "slug": p.stem,
                "kind": kind,
                "title": title,
                "date": _date(text) if kind == "md" else None,
                "mtime": p.stat().st_mtime,
            }
        )
    # Newest edit first: the doc being worked on is the one being read.
    docs.sort(key=lambda d: d["mtime"], reverse=True)
    return docs


@router.get("/research/{slug}")
def get_doc(slug: str) -> dict:
    p = _find(slug)
    if p is None:
        raise HTTPException(404, f"no research doc named {slug!r}")
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".md":
        return {"slug": slug, "kind": "md", "title": _md_title(text) or slug, "markdown": text}
    # HTML docs render in an iframe pointed at /raw; no body needed here.
    return {"slug": slug, "kind": "html", "title": _html_title(text) or slug, "markdown": None}


@router.get("/research/{slug}/raw")
def get_raw(slug: str) -> HTMLResponse:
    p = _find(slug)
    if p is None or p.suffix != ".html":
        raise HTTPException(404, f"no research page named {slug!r}")
    text = p.read_text(encoding="utf-8")
    # Artifact source files are bare page content (they start at <title>); give
    # the iframe a real document shell so charset and viewport behave.
    if not text.lstrip().lower().startswith("<!doctype"):
        text = (
            '<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"</head><body>{text}</body></html>"
        )
    return HTMLResponse(text)
