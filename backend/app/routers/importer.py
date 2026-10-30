"""/api/import — bring an AuTorrent and/or FileSplitter database into ScreenMate."""
from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from ..database import DATABASE_URL, engine
from ..services import importer

router = APIRouter(prefix="/import", tags=["import"])


@router.post("/inspect")
async def inspect(files: list[UploadFile] = File(...)):
    """Upload one or two databases; reports what each one contains. Nothing is changed yet."""
    out = []
    for f in files:
        try:
            token = await run_in_threadpool(importer.save_upload, f.file)
            info = await run_in_threadpool(importer.inspect_upload, token)
            out.append({"filename": f.filename, **info})
        except importer.ImportFailed as exc:
            out.append({"filename": f.filename, "error": str(exc)})
    return out


@router.post("/run")
def run(body: dict = Body(...)):
    tokens = body.get("tokens") or []
    if not tokens:
        raise HTTPException(status_code=400, detail="Upload a database first")
    rewrite = None
    rw = body.get("path_rewrite") or {}
    if rw.get("from") and rw.get("to"):
        if not (rw["from"].startswith("/") and rw["to"].startswith("/")):
            raise HTTPException(status_code=400, detail="Use absolute paths for the folder change")
        rewrite = (rw["from"], rw["to"])
    db_path = DATABASE_URL.replace("sqlite:///", "", 1) if DATABASE_URL.startswith("sqlite") else None
    try:
        return importer.run(engine, tokens,
                            overwrite_settings=bool(body.get("overwrite_settings")),
                            rewrite=rewrite, db_path=db_path)
    except importer.ImportFailed as exc:
        raise HTTPException(status_code=400, detail=str(exc))
