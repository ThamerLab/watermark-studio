from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import unicodedata
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

import pymupdf as fitz
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .batch import csv_template_bytes, output_pdf_name, parse_watermark_csv, zip_path
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
MAX_BATCH_FILES = int(os.getenv("MAX_BATCH_FILES", "20"))
MAX_BATCH_ROWS = int(os.getenv("MAX_BATCH_ROWS", "100"))
MAX_BATCH_OUTPUTS = int(os.getenv("MAX_BATCH_OUTPUTS", "500"))
MAX_BATCH_TOTAL_MB = int(os.getenv("MAX_BATCH_TOTAL_MB", "500"))
MAX_BATCH_TOTAL_BYTES = MAX_BATCH_TOTAL_MB * 1024 * 1024
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


async def read_small_upload(upload: UploadFile, limit: int) -> bytes:
    try:
        payload = await upload.read(limit + 1)
    finally:
        await upload.close()
    if len(payload) > limit:
        raise ProcessingError("ملف CSV كبير جدًا.")
    return payload


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
            request_limit = MAX_BATCH_TOTAL_BYTES if request.url.path.startswith("/api/batch/") else MAX_UPLOAD_BYTES
            if int(content_length) > request_limit + 10 * 1024 * 1024:
                limit_mb = MAX_BATCH_TOTAL_MB if request.url.path.startswith("/api/batch/") else MAX_UPLOAD_MB
                return JSONResponse({"detail": f"حجم الطلب يتجاوز الحد المسموح ({limit_mb} MB)."}, status_code=413)
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


@app.get("/api/batch/template")
async def batch_template():
    return Response(
        content=csv_template_bytes(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="watermark-studio-template.csv"'},
    )


@app.post("/api/batch/jobs", status_code=201)
async def create_batch_job(
    files: Annotated[list[UploadFile], File()],
    watermarks_csv: Annotated[UploadFile, File()],
):
    if not 1 <= len(files) <= MAX_BATCH_FILES:
        for upload in files:
            await upload.close()
        await watermarks_csv.close()
        raise ProcessingError(f"ارفع من ملف واحد إلى {MAX_BATCH_FILES} ملفات مصدر.")
    csv_payload = await read_small_upload(watermarks_csv, 2 * 1024 * 1024)
    try:
        rows = parse_watermark_csv(csv_payload, max_rows=MAX_BATCH_ROWS)
    except Exception:
        for upload in files:
            await upload.close()
        raise
    output_count = len(files) * len(rows)
    if output_count > MAX_BATCH_OUTPUTS:
        for upload in files:
            await upload.close()
        raise ProcessingError(f"عدد ملفات PDF الناتجة ({output_count}) يتجاوز الحد المسموح ({MAX_BATCH_OUTPUTS}).")

    job_id = str(uuid4())
    path = JOBS_DIR / job_id
    sources_dir = path / "sources"
    sources_dir.mkdir(parents=True, exist_ok=False)
    total_size = 0
    sources: list[dict[str, object]] = []
    try:
        (path / "watermarks.csv").write_bytes(csv_payload)
        for index, upload in enumerate(files):
            original_name = safe_name(upload.filename)
            extension = Path(original_name).suffix.lower()
            if extension not in ALLOWED_EXTENSIONS:
                raise ProcessingError(f"الملف {original_name}: الأنواع المدعومة هي PDF وPPTX فقط.")
            source_dir = sources_dir / f"{index:03d}"
            source_dir.mkdir()
            source = source_dir / f"source{extension}"
            size = await save_upload(upload, source)
            total_size += size
            if total_size > MAX_BATCH_TOTAL_BYTES:
                raise ProcessingError(f"إجمالي الملفات يتجاوز الحد المسموح ({MAX_BATCH_TOTAL_MB} MB).")
            page_count = await asyncio.to_thread(validate_document, source, extension)
            base_pdf = source
            if extension == ".pptx":
                converted = await asyncio.to_thread(convert_pptx, source, source_dir, LIBREOFFICE_TIMEOUT)
                base_pdf = source_dir / "base.pdf"
                converted.replace(base_pdf)
                with fitz.open(base_pdf) as document:
                    page_count = document.page_count
            sources.append(
                {
                    "filename": original_name,
                    "extension": extension,
                    "size": size,
                    "page_count": page_count,
                    "base_pdf": str(base_pdf.relative_to(path)).replace("\\", "/"),
                }
            )
        await asyncio.to_thread(render_first_page, path / str(sources[0]["base_pdf"]), path / "preview.png")
        metadata = {
            "id": job_id,
            "kind": "batch",
            "sources": sources,
            "watermarks": [row.as_dict() for row in rows],
            "source_count": len(sources),
            "watermark_count": len(rows),
            "output_count": output_count,
            "total_size": total_size,
            "created_at": int(time.time()),
        }
        (path / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return {**metadata, "preview_url": f"/api/jobs/{job_id}/preview"}
    except Exception:
        for upload in files:
            await upload.close()
        shutil.rmtree(path, ignore_errors=True)
        raise


def generate_batch_archive(
    path: Path,
    metadata: dict,
    *,
    font_name: str,
    font_size: int,
    opacity: float,
    rotation: int,
    color: str,
    position: str,
    page_mode: str,
    custom_pages: str,
) -> int:
    sources = metadata["sources"]
    watermarks = metadata["watermarks"]
    selected_by_source: list[set[int]] = []
    for source in sources:
        try:
            selected_by_source.append(parse_pages(page_mode, custom_pages, int(source["page_count"])))
        except ProcessingError as exc:
            raise ProcessingError(f"الملف {source['filename']}: {exc}") from exc

    temporary_zip = path / "batch.tmp.zip"
    final_zip = path / "batch.zip"
    temporary_pdf = path / "batch-output.tmp.pdf"
    temporary_zip.unlink(missing_ok=True)
    temporary_pdf.unlink(missing_ok=True)
    source_counts: dict[str, int] = {}
    source_numbers: list[int] = []
    for source in sources:
        key = Path(str(source["filename"])).stem.casefold()
        source_counts[key] = source_counts.get(key, 0) + 1
        source_numbers.append(source_counts[key])
    generated = 0
    try:
        with zipfile.ZipFile(temporary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for watermark in watermarks:
                payload = make_text_watermark(
                    str(watermark["watermark_text"]),
                    font_size,
                    color,
                    opacity,
                    rotation,
                    font_name,
                )
                output_name = str(watermark["output_name"])
                for index, source in enumerate(sources):
                    source_pdf = (path / str(source["base_pdf"])).resolve()
                    if path not in source_pdf.parents:
                        raise ProcessingError("مسار ملف المصدر غير صالح.")
                    create_watermarked_pdf(
                        source_pdf,
                        temporary_pdf,
                        selected_by_source[index],
                        text_payload=payload,
                        text_position=position,
                        image_payload=None,
                        image_position="center",
                        image_scale=25,
                    )
                    filename = output_pdf_name(str(source["filename"]), output_name, duplicate_number=source_numbers[index])
                    archive.write(temporary_pdf, zip_path(output_name, filename))
                    temporary_pdf.unlink()
                    generated += 1
        temporary_zip.replace(final_zip)
        return generated
    finally:
        temporary_pdf.unlink(missing_ok=True)
        temporary_zip.unlink(missing_ok=True)


@app.post("/api/batch/jobs/{job_id}/generate")
async def generate_batch(
    job_id: str,
    font_name: Annotated[str, Form()] = "noto-sans",
    font_size: Annotated[int, Form()] = 48,
    opacity: Annotated[float, Form()] = 0.25,
    rotation: Annotated[int, Form()] = -35,
    color: Annotated[str, Form()] = "#1f2937",
    position: Annotated[str, Form()] = "center",
    page_mode: Annotated[str, Form()] = "all",
    custom_pages: Annotated[str, Form()] = "",
):
    path = job_dir(job_id)
    metadata = load_metadata(path)
    if metadata.get("kind") != "batch":
        raise HTTPException(404, "مهمة الدفعة غير موجودة.")
    lock = job_locks.setdefault(job_id, asyncio.Lock())
    async with lock:
        generated = await asyncio.to_thread(
            generate_batch_archive,
            path,
            metadata,
            font_name=font_name,
            font_size=font_size,
            opacity=opacity,
            rotation=rotation,
            color=color,
            position=position,
            page_mode=page_mode,
            custom_pages=custom_pages,
        )
    return {"status": "ready", "generated_count": generated, "download_url": f"/api/batch/jobs/{job_id}/download"}


@app.get("/api/batch/jobs/{job_id}/download")
async def download_batch(job_id: str):
    path = job_dir(job_id)
    metadata = load_metadata(path)
    if metadata.get("kind") != "batch":
        raise HTTPException(404, "مهمة الدفعة غير موجودة.")
    archive = path / "batch.zip"
    if not archive.is_file():
        raise HTTPException(404, "أنشئ ملفات الدفعة أولًا.")
    return FileResponse(archive, media_type="application/zip", filename="watermark-studio-batch.zip")


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
    font_name: Annotated[str, Form()] = "noto-sans",
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
            text_payload = await asyncio.to_thread(make_text_watermark, watermark_text, font_size, color, opacity, rotation, font_name)
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
