import io
import zipfile
from pathlib import Path

import pymupdf as fitz
import pytest

from app.batch import csv_template_bytes, output_pdf_name, parse_watermark_csv, safe_output_name
from app.processing import ProcessingError


def test_generic_csv_parsing_preserves_arbitrary_text_and_extra_columns():
    payload = (
        "\ufeffwatermark_text,output_name,future_column\r\n"
        '"نسخة خاصة - للاستخدام الداخلي فقط","نسخة داخلية","قيمة مستقبلية"\r\n'
        '"CONFIDENTIAL - ABC Company",,"future"\r\n'
    ).encode("utf-8")
    rows = parse_watermark_csv(payload, max_rows=10)
    assert rows[0].watermark_text == "نسخة خاصة - للاستخدام الداخلي فقط"
    assert rows[0].output_name == "نسخة داخلية"
    assert rows[0].extra == {"future_column": "قيمة مستقبلية"}
    assert rows[1].output_name == "CONFIDENTIAL - ABC Company"


def test_output_name_is_safe_but_preserves_arabic_and_text_is_not_truncated():
    long_text = "إدارة المراجعة المالية " * 20
    payload = f"watermark_text,output_name\n\"{long_text}\",\n".encode("utf-8")
    row = parse_watermark_csv(payload, max_rows=2)[0]
    assert row.watermark_text == long_text.strip()
    assert len(row.output_name) <= 80
    assert row.output_name.startswith("إدارة المراجعة المالية")
    assert safe_output_name('محمد أحمد:/\\*?"<>|') == "محمد أحمد"
    assert safe_output_name("CON.txt") == "_CON.txt"
    assert "\u202e" not in safe_output_name("ملف\u202eخفي")
    assert output_pdf_name("Course Guide.pdf", "محمد أحمد") == "Course Guide - محمد أحمد.pdf"


def test_duplicate_output_names_become_unique():
    rows = parse_watermark_csv(
        b"watermark_text,output_name\nFirst,Shared\nSecond,Shared\n",
        max_rows=5,
    )
    assert [row.output_name for row in rows] == ["Shared", "Shared (2)"]


def test_csv_template_has_utf8_bom_and_exact_columns():
    payload = csv_template_bytes()
    assert payload.startswith(b"\xef\xbb\xbf")
    text = payload.decode("utf-8-sig")
    assert text.splitlines() == [
        "watermark_text,output_name",
        '"نسخة خاصة - محمد أحمد","محمد أحمد"',
        '"للاستخدام الداخلي فقط","نسخة داخلية"',
    ]
    rows = parse_watermark_csv(payload, max_rows=10)
    assert [row.watermark_text for row in rows] == ["نسخة خاصة - محمد أحمد", "للاستخدام الداخلي فقط"]


def test_csv_requires_watermark_text():
    with pytest.raises(ProcessingError):
        parse_watermark_csv(b"name,output_name\nValue,Folder\n", max_rows=5)
    with pytest.raises(ProcessingError):
        parse_watermark_csv(b"watermark_text,output_name\n,Folder\n", max_rows=5)
