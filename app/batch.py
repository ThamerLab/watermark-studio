from __future__ import annotations

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .processing import ProcessingError


UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_FOLDER_LENGTH = 60
MAX_FILE_STEM_LENGTH = 105
MAX_ZIP_PATH_LENGTH = 175
FILENAME_MODES = {"source", "source-and-output"}


@dataclass(frozen=True)
class WatermarkRow:
    watermark_text: str
    output_name: str
    extra: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"watermark_text": self.watermark_text, "output_name": self.output_name, "extra": self.extra}


def safe_output_name(value: str, *, max_length: int = MAX_FOLDER_LENGTH, fallback: str = "watermark") -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = "".join("_" if unicodedata.category(character) in {"Cc", "Cf"} else character for character in normalized)
    cleaned = UNSAFE_FILENAME.sub("_", normalized)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > max_length:
        shortened = cleaned[:max_length]
        word_boundary = shortened.rfind(" ")
        if word_boundary >= int(max_length * 0.7):
            shortened = shortened[:word_boundary]
        cleaned = shortened.rstrip(" ._")
    return cleaned or fallback


def unique_names(values: list[str], *, max_length: int = MAX_FOLDER_LENGTH) -> list[str]:
    used: set[str] = set()
    result: list[str] = []
    for value in values:
        candidate = value
        suffix = 2
        while candidate.casefold() in used:
            marker = f" ({suffix})"
            candidate = f"{value[: max(1, max_length - len(marker))].rstrip()}{marker}"
            suffix += 1
        used.add(candidate.casefold())
        result.append(candidate)
    return result


def parse_watermark_csv(payload: bytes, *, max_rows: int) -> list[WatermarkRow]:
    if not payload:
        raise ProcessingError("ملف CSV فارغ.")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProcessingError("يجب حفظ ملف CSV بترميز UTF-8.") from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if not reader.fieldnames:
            raise ProcessingError("ملف CSV بلا عناوين أعمدة.")
        fieldnames = [(name or "").strip().lstrip("\ufeff") for name in reader.fieldnames]
        reader.fieldnames = fieldnames
        if "watermark_text" not in fieldnames:
            raise ProcessingError("العمود المطلوب watermark_text غير موجود.")
        raw_rows: list[tuple[str, str, dict[str, str]]] = []
        for line_number, row in enumerate(reader, start=2):
            watermark_text = (row.get("watermark_text") or "").strip()
            if not watermark_text:
                raise ProcessingError(f"نص العلامة المائية مفقود في صف CSV رقم {line_number}.")
            if len(watermark_text) > 1000:
                raise ProcessingError(f"نص العلامة في صف CSV رقم {line_number} يتجاوز 1000 حرف.")
            requested_name = (row.get("output_name") or "").strip()
            extra = {key: (value or "").strip() for key, value in row.items() if key not in {"watermark_text", "output_name"} and key}
            raw_rows.append((watermark_text, safe_output_name(requested_name or watermark_text, max_length=MAX_FOLDER_LENGTH), extra))
            if len(raw_rows) > max_rows:
                raise ProcessingError(f"عدد العلامات المائية يتجاوز الحد المسموح ({max_rows}).")
    except csv.Error as exc:
        raise ProcessingError("تعذر قراءة ملف CSV؛ تحقق من الفواصل وعلامات الاقتباس.") from exc
    if not raw_rows:
        raise ProcessingError("أضف نص علامة مائية واحدًا على الأقل إلى CSV.")
    names = unique_names([row[1] for row in raw_rows])
    return [WatermarkRow(text, names[index], extra) for index, (text, _, extra) in enumerate(raw_rows)]


def csv_template_bytes() -> bytes:
    stream = io.StringIO(newline="")
    stream.write("watermark_text,output_name\r\n")
    writer = csv.writer(stream, lineterminator="\r\n", quoting=csv.QUOTE_ALL)
    writer.writerow(["نسخة خاصة - محمد أحمد", "محمد أحمد"])
    writer.writerow(["للاستخدام الداخلي فقط", "نسخة داخلية"])
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def clean_source_stem(source_filename: str, *, max_length: int = MAX_FILE_STEM_LENGTH) -> str:
    source_stem = Path(source_filename).stem
    previous = None
    while source_stem != previous:
        previous = source_stem
        source_stem = re.sub(r"(?i)(?:[_ .-]+sanitized)+$", "", source_stem).rstrip(" ._-")
        source_stem = re.sub(r"(?i)\.(?:pdf|pptx)$", "", source_stem).rstrip(" ._-")
    return safe_output_name(source_stem, max_length=max_length, fallback="document")


def output_pdf_name(
    source_filename: str,
    output_name: str,
    *,
    duplicate_number: int | None = None,
    include_output_name: bool = False,
) -> str:
    source_stem = clean_source_stem(source_filename)
    if duplicate_number and duplicate_number > 1:
        marker = f" ({duplicate_number})"
        source_stem = f"{source_stem[: MAX_FILE_STEM_LENGTH - len(marker)].rstrip()}{marker}"
    if include_output_name:
        suffix = safe_output_name(output_name, max_length=MAX_FOLDER_LENGTH)
        source_stem = safe_output_name(f"{source_stem} - {suffix}", max_length=MAX_FILE_STEM_LENGTH, fallback="document")
    return source_stem + ".pdf"


def source_pdf_names(source_filenames: list[str], output_name: str, filename_mode: str = "source") -> list[str]:
    if filename_mode not in FILENAME_MODES:
        raise ProcessingError("نمط أسماء الملفات غير صالح.")
    include_output = filename_mode == "source-and-output"
    full_stems = [
        safe_output_name(
            f"{clean_source_stem(filename, max_length=300)} - {safe_output_name(output_name)}" if include_output else clean_source_stem(filename, max_length=300),
            max_length=300,
            fallback="document",
        )
        for filename in source_filenames
    ]
    used: dict[str, str] = {}
    duplicate_counts: dict[str, int] = {}
    result: list[str] = []
    for original, full_stem in zip(source_filenames, full_stems, strict=True):
        stem = safe_output_name(full_stem, max_length=MAX_FILE_STEM_LENGTH, fallback="document")
        candidate = stem
        folded = candidate.casefold()
        if folded in used:
            if used[folded].casefold() == full_stem.casefold():
                duplicate_counts[folded] = duplicate_counts.get(folded, 1) + 1
                marker = f" ({duplicate_counts[folded]})"
            else:
                marker = f" - {hashlib.sha256(original.encode('utf-8')).hexdigest()[:6]}"
            candidate = f"{stem[: MAX_FILE_STEM_LENGTH - len(marker)].rstrip()}{marker}"
            counter = 2
            while candidate.casefold() in used:
                numeric = f" ({counter})"
                candidate = f"{stem[: MAX_FILE_STEM_LENGTH - len(numeric)].rstrip()}{numeric}"
                counter += 1
        used[candidate.casefold()] = full_stem
        result.append(candidate + ".pdf")
    return result


def zip_path(folder: str, filename: str) -> str:
    safe_folder = safe_output_name(folder, max_length=MAX_FOLDER_LENGTH)
    available_stem = min(MAX_FILE_STEM_LENGTH, MAX_ZIP_PATH_LENGTH - len(safe_folder) - len("/.pdf"))
    safe_file = safe_output_name(Path(filename).stem, max_length=max(20, available_stem), fallback="document") + ".pdf"
    return str(PurePosixPath(safe_folder) / safe_file)


def manifest_csv_bytes(entries: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=["output_folder", "generated_file", "original_file", "watermark_text"],
        lineterminator="\r\n",
        quoting=csv.QUOTE_ALL,
    )
    writer.writeheader()
    writer.writerows(entries)
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")
