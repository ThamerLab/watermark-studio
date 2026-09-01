from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .processing import ProcessingError


UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


@dataclass(frozen=True)
class WatermarkRow:
    watermark_text: str
    output_name: str
    extra: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {"watermark_text": self.watermark_text, "output_name": self.output_name, "extra": self.extra}


def safe_output_name(value: str, *, max_length: int = 80, fallback: str = "watermark") -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = "".join("_" if unicodedata.category(character) in {"Cc", "Cf"} else character for character in normalized)
    cleaned = UNSAFE_FILENAME.sub("_", normalized)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" ._")
    return cleaned or fallback


def unique_names(values: list[str]) -> list[str]:
    used: set[str] = set()
    result: list[str] = []
    for value in values:
        candidate = value
        suffix = 2
        while candidate.casefold() in used:
            marker = f" ({suffix})"
            candidate = f"{value[: max(1, 80 - len(marker))].rstrip()}{marker}"
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
            raw_rows.append((watermark_text, safe_output_name(requested_name or watermark_text), extra))
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


def output_pdf_name(source_filename: str, output_name: str, *, duplicate_number: int | None = None) -> str:
    source_stem = safe_output_name(Path(source_filename).stem, max_length=90, fallback="document")
    if duplicate_number and duplicate_number > 1:
        source_stem = safe_output_name(f"{source_stem} ({duplicate_number})", max_length=90)
    suffix = safe_output_name(output_name, max_length=80)
    return safe_output_name(f"{source_stem} - {suffix}", max_length=180) + ".pdf"


def zip_path(folder: str, filename: str) -> str:
    safe_folder = safe_output_name(folder)
    safe_file = safe_output_name(Path(filename).stem, max_length=180) + ".pdf"
    return str(PurePosixPath(safe_folder) / safe_file)
