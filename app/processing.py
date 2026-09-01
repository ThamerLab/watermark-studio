from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import pymupdf as fitz
from PIL import Image, ImageColor, ImageDraw, ImageFont, UnidentifiedImageError


class ProcessingError(ValueError):
    """A safe, user-facing document processing error."""


def validate_document(path: Path, extension: str, max_unpacked_mb: int = 500) -> int | None:
    header = path.read_bytes()[:8]
    if extension == ".pdf":
        if not header.startswith(b"%PDF-"):
            raise ProcessingError("محتوى الملف لا يطابق صيغة PDF.")
        try:
            with fitz.open(path) as document:
                if not document.is_pdf or document.page_count < 1:
                    raise ProcessingError("ملف PDF غير صالح أو بلا صفحات.")
                return document.page_count
        except ProcessingError:
            raise
        except Exception as exc:
            raise ProcessingError("تعذر قراءة ملف PDF.") from exc

    if extension == ".pptx":
        if not header.startswith(b"PK"):
            raise ProcessingError("محتوى الملف لا يطابق صيغة PPTX.")
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                    raise ProcessingError("ملف PowerPoint غير صالح.")
                unpacked = sum(item.file_size for item in archive.infolist())
                if unpacked > max_unpacked_mb * 1024 * 1024:
                    raise ProcessingError("محتوى ملف PowerPoint بعد فك الضغط كبير جدًا.")
        except ProcessingError:
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            raise ProcessingError("ملف PowerPoint تالف أو غير صالح.") from exc
        return None

    raise ProcessingError("نوع الملف غير مدعوم.")


def convert_pptx(source: Path, output_dir: Path, timeout_seconds: int) -> Path:
    profile_dir = output_dir / "libreoffice-profile"
    profile_dir.mkdir(exist_ok=True)
    command = [
        "libreoffice",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--headless",
        "--nologo",
        "--nodefault",
        "--nofirststartwizard",
        "--nolockcheck",
        "--convert-to",
        "pdf",
        "--outdir",
        str(output_dir),
        str(source),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            timeout=timeout_seconds,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "HOME": str(output_dir)},
        )
    except subprocess.TimeoutExpired as exc:
        raise ProcessingError("انتهت مهلة تحويل PowerPoint.") from exc
    except (subprocess.CalledProcessError, OSError) as exc:
        raise ProcessingError("فشل تحويل PowerPoint إلى PDF.") from exc
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

    converted = output_dir / f"{source.stem}.pdf"
    if not converted.is_file() or converted.stat().st_size == 0:
        raise ProcessingError("لم يُنتج LibreOffice ملف PDF صالحًا.")
    validate_document(converted, ".pdf")
    return converted


def render_first_page(pdf_path: Path, output_path: Path) -> None:
    try:
        with fitz.open(pdf_path) as document:
            page = document[0]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            pixmap.save(output_path)
    except Exception as exc:
        raise ProcessingError("تعذر إنشاء المعاينة.") from exc


def parse_pages(mode: str, expression: str, page_count: int) -> set[int]:
    if mode == "all":
        return set(range(page_count))
    if mode == "first":
        return {0}
    if mode != "custom":
        raise ProcessingError("خيار الصفحات غير صالح.")
    compact = re.sub(r"\s+", "", expression or "")
    if not compact or not re.fullmatch(r"[0-9,-]+", compact):
        raise ProcessingError("اكتب الصفحات مثل: 1-3,7,10-12")
    pages: set[int] = set()
    for part in compact.split(","):
        if not part:
            raise ProcessingError("صيغة الصفحات المخصصة غير صالحة.")
        if "-" in part:
            bits = part.split("-")
            if len(bits) != 2 or not all(bits):
                raise ProcessingError("صيغة نطاق الصفحات غير صالحة.")
            start, end = map(int, bits)
            if start > end:
                raise ProcessingError("بداية نطاق الصفحات أكبر من نهايته.")
            pages.update(range(start - 1, end))
        else:
            pages.add(int(part) - 1)
    if not pages or min(pages) < 0 or max(pages) >= page_count:
        raise ProcessingError(f"يجب أن تكون الصفحات بين 1 و {page_count}.")
    return pages


FONT_FILES = {
    "noto-sans": "NotoSansArabic-Regular.ttf",
    "noto-naskh": "NotoNaskhArabic-Regular.ttf",
    "noto-kufi": "NotoKufiArabic-Regular.ttf",
    "carlito": "Carlito-Regular.ttf",
}


@lru_cache(maxsize=8)
def _font_path(font_name: str = "noto-sans") -> str:
    if font_name not in FONT_FILES:
        raise ProcessingError("الخط المحدد غير صالح.")
    configured = os.getenv("WATERMARK_FONT_PATH", "")
    filename = FONT_FILES[font_name]
    candidates = [
        configured,
        f"/usr/share/fonts/truetype/noto/{filename}",
        f"/usr/share/fonts/opentype/noto/{filename}",
        f"/usr/share/fonts/truetype/crosextra/{filename}",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ProcessingError("لم يُعثر على خط يدعم العربية.")


def _has_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06ff]", text))


def make_text_watermark(
    text: str,
    font_size: int,
    color: str,
    opacity: float,
    rotation: int,
    font_name: str = "noto-sans",
) -> bytes:
    if not text.strip() or len(text) > 1000:
        raise ProcessingError("نص العلامة مطلوب ويجب ألا يتجاوز 1000 حرف.")
    if not 8 <= font_size <= 200:
        raise ProcessingError("حجم الخط يجب أن يكون بين 8 و200.")
    if not 0.05 <= opacity <= 1:
        raise ProcessingError("الشفافية غير صالحة.")
    try:
        rgb = ImageColor.getrgb(color)
    except ValueError as exc:
        raise ProcessingError("لون العلامة غير صالح.") from exc

    scale = 2
    font = ImageFont.truetype(_font_path(font_name), font_size * scale)
    lines = text.splitlines() or [text]
    scratch = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(scratch)
    measurements: list[tuple[str, float, float, str | None]] = []
    for line in lines:
        content = line or " "
        direction = "rtl" if _has_arabic(content) else None
        try:
            box = draw.textbbox((0, 0), content, font=font, direction=direction)
        except (KeyError, ValueError):
            direction = None
            box = draw.textbbox((0, 0), content, font=font)
        measurements.append((content, box[2] - box[0], box[3] - box[1], direction))
    padding = font_size * scale
    line_gap = max(4, font_size * scale // 4)
    width = int(max(item[1] for item in measurements) + padding * 2)
    height = int(sum(item[2] for item in measurements) + line_gap * (len(lines) - 1) + padding * 2)
    canvas = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    y = padding
    alpha = round(255 * opacity)
    for content, line_width, line_height, direction in measurements:
        x = (width - line_width) / 2
        kwargs = {"font": font, "fill": (*rgb, alpha)}
        if direction:
            kwargs["direction"] = direction
        draw.text((x, y), content, **kwargs)
        y += line_height + line_gap
    if rotation:
        canvas = canvas.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
    stream = io.BytesIO()
    canvas.save(stream, format="PNG", optimize=True)
    return stream.getvalue()


def normalize_image_watermark(source: Path, opacity: float) -> bytes:
    if not 0.05 <= opacity <= 1:
        raise ProcessingError("شفافية الصورة غير صالحة.")
    try:
        with Image.open(source) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ProcessingError("صورة العلامة يجب أن تكون PNG أو JPEG.")
            image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            normalized = image.convert("RGBA")
            alpha = normalized.getchannel("A").point(lambda value: round(value * opacity))
            normalized.putalpha(alpha)
            stream = io.BytesIO()
            normalized.save(stream, format="PNG", optimize=True)
            return stream.getvalue()
    except ProcessingError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ProcessingError("صورة العلامة غير صالحة.") from exc


POSITIONS = {"center", "top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right", "tiled"}


def _rect_for(page: fitz.Page, image_width: float, image_height: float, position: str, width_points: float | None = None) -> fitz.Rect:
    if position not in POSITIONS:
        raise ProcessingError("موضع العلامة غير صالح.")
    page_rect = page.rect
    if width_points:
        ratio = image_height / max(image_width, 1)
        width, height = width_points, width_points * ratio
    else:
        width, height = image_width / 2, image_height / 2
    max_width = page_rect.width * 0.9
    if width > max_width:
        height *= max_width / width
        width = max_width
    margin = min(page_rect.width, page_rect.height) * 0.05
    horizontal = {
        "left": margin,
        "center": (page_rect.width - width) / 2,
        "right": page_rect.width - width - margin,
    }
    vertical = {"top": margin, "center": (page_rect.height - height) / 2, "bottom": page_rect.height - height - margin}
    if position == "center":
        x, y = horizontal["center"], vertical["center"]
    elif position == "tiled":
        x, y = margin, margin
    else:
        v, h = position.split("-")
        x, y = horizontal[h], vertical[v]
    return fitz.Rect(x, y, x + width, y + height)


def _insert(page: fitz.Page, payload: bytes, position: str, width_points: float | None = None) -> None:
    pixmap = fitz.Pixmap(payload)
    rect = _rect_for(page, pixmap.width, pixmap.height, position, width_points)
    if position != "tiled":
        page.insert_image(rect, stream=payload, overlay=True)
        return
    gap_x, gap_y = rect.width * 0.45, rect.height * 0.65
    y = -rect.height * 0.2
    row = 0
    while y < page.rect.height:
        x = (-rect.width * 0.4) if row % 2 else 0
        while x < page.rect.width:
            tile = fitz.Rect(x, y, x + rect.width, y + rect.height)
            page.insert_image(tile, stream=payload, overlay=True)
            x += rect.width + gap_x
        y += rect.height + gap_y
        row += 1


def create_watermarked_pdf(
    source_pdf: Path,
    output_pdf: Path,
    selected_pages: Iterable[int],
    *,
    text_payload: bytes | None,
    text_position: str,
    image_payload: bytes | None,
    image_position: str,
    image_scale: int,
) -> None:
    if text_payload is None and image_payload is None:
        raise ProcessingError("أضف نصًا أو صورة للعلامة المائية.")
    if not 5 <= image_scale <= 80:
        raise ProcessingError("حجم صورة العلامة يجب أن يكون بين 5% و80%.")
    pages = set(selected_pages)
    try:
        with fitz.open(source_pdf) as document:
            for number in pages:
                page = document[number]
                if text_payload:
                    _insert(page, text_payload, text_position)
                if image_payload:
                    _insert(page, image_payload, image_position, page.rect.width * image_scale / 100)
            document.save(output_pdf, garbage=4, deflate=True, clean=True)
    except ProcessingError:
        raise
    except Exception as exc:
        raise ProcessingError("تعذر إنشاء ملف PDF النهائي.") from exc
