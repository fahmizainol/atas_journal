"""Import ATAS .xlsx exports: from the watched dir, or via upload."""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from journal import ingest
from journal.config import IMPORTS_DIR

from .. import deps

router = APIRouter()


@router.post("/import/dir")
def import_dir() -> dict:
    conn = deps.get_conn()
    with deps.db_lock():
        res = ingest.import_dir(conn)
    total_fills = sum(c["executions"] for c in res.values())
    return {"files": len(res), "total_fills": total_fills, "detail": res}


@router.post("/import/upload")
async def import_upload(files: list[UploadFile] = File(...)) -> dict:
    conn = deps.get_conn()
    results: dict[str, dict] = {}
    for uf in files:
        dest = IMPORTS_DIR / uf.filename
        dest.write_bytes(await uf.read())
        with deps.db_lock():
            results[uf.filename] = ingest.import_file(conn, dest)
    return {"results": results}
