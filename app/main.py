from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import unicodedata
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import pymupdf as fitz
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .processing import (
    ProcessingError,
    convert_pptx,
    create_watermarked_pdf,
    make_text_watermark,
    normalize_image_watermark,
    parse_pages,
    render_first_page,
    validate_document,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR.parent / "data")).resolve()
JOBS_DIR = DATA_DIR / "jobs"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
RETENTION_MINUTES = int(os.getenv("FILE_RETENTION_MINUTES", "60"))
LIBREOFFICE_TIMEOUT = int(os.getenv("LIBREOFFICE_TIMEOUT_SECONDS", "180"))
CHUNK_SIZE = 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".pptx"}
UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
job_locks: dict[str, asyncio.Lock] = {}


def safe_name(filename: str | None) -> str:
    name = Path((filename or "document").replace("\\", "/")).name
    name = unicodedata.normalize("NFKC", name)
    cleaned = UNSAFE_NAME.sub("_", name).strip(" ._")
    return (cleaned or "document")[:120]


def job_dir(job_id: str) -> Path:
    try:
        canonical = str(UUID(job_id))
    except ValueError as exc:
        raise HTTPException(404, "المهمة غير موجودة.") from exc
    path = (JOBS_DIR / canonical).resolve()
    if path.parent != JOBS_DIR:
        raise HTTPException(404, "المهمة غير موجودة.")
    return path


def load_metadata(path: Path) -> dict:
    try:
        return json.loads((path / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(404, "المهمة غير موجودة أو انتهت صلاحيتها.") from exc


async def save_upload(upload: UploadFile, destination: Path, limit: int = MAX_UPLOAD_BYTES) -> int:
    total = 0
    try:
        with destination.open("xb") as stream:
            while chunk := await upload.read(CHUNK_SIZE):
                total += len(chunk)
                if total > limit:
                    raise ProcessingError(f"حجم الملف يتجاوز الحد المسموح ({MAX_UPLOAD_MB} MB).")
                stream.write(chunk)
    finally:
        await upload.close()
    if total == 0:
        raise ProcessingError("الملف فارغ.")
    return total


def cleanup_expired() -> None:
    cutoff = time.time() - RETENTION_MINUTES * 60
    if not JOBS_DIR.exists():
        return
    for path in JOBS_DIR.iterdir():
        if path.is_dir() and path.stat().st_mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)


async def cleanup_loop() -> None:
    while True:
        await asyncio.sleep(60)
        await asyncio.to_thread(cleanup_expired)


@asynccontextmanager
async def lifespan(_: FastAPI):
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(cleanup_expired)
    task = asyncio.create_task(cleanup_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="Watermark Studio", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.middleware("http")
async def security_and_limits(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_UPLOAD_BYTES + 10 * 1024 * 1024:
                return JSONResponse({"detail": f"حجم الطلب يتجاوز الحد المسموح ({MAX_UPLOAD_MB} MB)."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "قيمة Content-Length غير صالحة."}, status_code=400)
    response = await call_next(request)
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'",
            "Cache-Control": "no-store",
        }
    )
    return response


@app.exception_handler(ProcessingError)
async def processing_error(_: Request, exc: ProcessingError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/api/jobs", status_code=201)
async def create_job(file: Annotated[UploadFile, File()]):
    original_name = safe_name(file.filename)
    extension = Path(original_name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ProcessingError("الأنواع المدعومة هي PDF وPPTX فقط.")
    job_id = str(uuid4())
    path = JOBS_DIR / job_id
    path.mkdir(parents=True, exist_ok=False)
    source = path / f"source{extension}"
    try:
        size = await save_upload(file, source)
        page_count = await asyncio.to_thread(validate_document, source, extension)
        working_pdf = source
        if extension == ".pptx":
            converted = await asyncio.to_thread(convert_pptx, source, path, LIBREOFFICE_TIMEOUT)
            working_pdf = path / "working.pdf"
            converted.replace(working_pdf)
            with fitz.open(working_pdf) as document:
                page_count = document.page_count
        preview = path / "preview.png"
        await asyncio.to_thread(render_first_page, working_pdf, preview)
        metadata = {
            "id": job_id,
            "filename": original_name,
            "extension": extension,
            "size": size,
            "page_count": page_count,
            "created_at": int(time.time()),
        }
        (path / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return {**metadata, "preview_url": f"/api/jobs/{job_id}/preview"}
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise


@app.get("/api/jobs/{job_id}/preview")
async def preview(job_id: str):
    path = job_dir(job_id)
    load_metadata(path)
    preview_path = path / "preview.png"
    if not preview_path.is_file():
        raise HTTPException(404, "المعاينة غير موجودة.")
    return FileResponse(preview_path, media_type="image/png")


@app.post("/api/jobs/{job_id}/generate")
async def generate(
    job_id: str,
    watermark_text: Annotated[str, Form()] = "",
    font_size: Annotated[int, Form()] = 48,
    opacity: Annotated[float, Form()] = 0.25,
    rotation: Annotated[int, Form()] = -35,
    color: Annotated[str, Form()] = "#1f2937",
    position: Annotated[str, Form()] = "center",
    page_mode: Annotated[str, Form()] = "all",
    custom_pages: Annotated[str, Form()] = "",
    image_position: Annotated[str, Form()] = "center",
    image_scale: Annotated[int, Form()] = 25,
    image_opacity: Annotated[float, Form()] = 0.3,
    image_watermark: Annotated[UploadFile | None, File()] = None,
):
    path = job_dir(job_id)
    metadata = load_metadata(path)
    source_pdf = path / ("source.pdf" if metadata["extension"] == ".pdf" else "working.pdf")
    lock = job_locks.setdefault(job_id, asyncio.Lock())
    async with lock:
        selected = parse_pages(page_mode, custom_pages, int(metadata["page_count"]))
        text_payload = None
        if watermark_text.strip():
            text_payload = await asyncio.to_thread(make_text_watermark, watermark_text, font_size, color, opacity, rotation)
        image_payload = None
        image_path = path / "watermark-upload"
        try:
            if image_watermark and image_watermark.filename:
                await save_upload(image_watermark, image_path, min(MAX_UPLOAD_BYTES, 20 * 1024 * 1024))
                image_payload = await asyncio.to_thread(normalize_image_watermark, image_path, image_opacity)
            output = path / "final.pdf"
            temporary = path / "final.tmp.pdf"
            temporary.unlink(missing_ok=True)
            await asyncio.to_thread(
                create_watermarked_pdf,
                source_pdf,
                temporary,
                selected,
                text_payload=text_payload,
                text_position=position,
                image_payload=image_payload,
                image_position=image_position,
                image_scale=image_scale,
            )
            temporary.replace(output)
        finally:
            image_path.unlink(missing_ok=True)
        return {"status": "ready", "download_url": f"/api/jobs/{job_id}/download"}


@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str):
    path = job_dir(job_id)
    metadata = load_metadata(path)
    output = path / "final.pdf"
    if not output.is_file():
        raise HTTPException(404, "أنشئ ملف PDF أولًا.")
    stem = Path(metadata["filename"]).stem[:80]
    return FileResponse(output, media_type="application/pdf", filename=f"{stem}-watermarked.pdf")


@app.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str):
    path = job_dir(job_id)
    if not path.is_dir():
        raise HTTPException(404, "المهمة غير موجودة.")
    shutil.rmtree(path)
    job_locks.pop(job_id, None)
