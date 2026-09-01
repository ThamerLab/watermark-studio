import io
import subprocess
import zipfile
from pathlib import Path

import pymupdf as fitz
import pytest
from PIL import Image

from app import processing


def make_pdf(path: Path, pages: int = 3) -> None:
    document = fitz.open()
    for number in range(pages):
        page = document.new_page(width=400, height=600)
        page.insert_text((50, 80), f"Page {number + 1}")
    document.save(path)
    document.close()


def test_parse_pages():
    assert processing.parse_pages("all", "", 5) == {0, 1, 2, 3, 4}
    assert processing.parse_pages("first", "", 5) == {0}
    assert processing.parse_pages("custom", "1-3, 5", 6) == {0, 1, 2, 4}
    with pytest.raises(processing.ProcessingError):
        processing.parse_pages("custom", "3-1", 5)
    with pytest.raises(processing.ProcessingError):
        processing.parse_pages("custom", "1,9", 5)


def test_pdf_validation_and_signature(tmp_path):
    valid = tmp_path / "valid.pdf"
    make_pdf(valid, 2)
    assert processing.validate_document(valid, ".pdf") == 2
    fake = tmp_path / "fake.pdf"
    fake.write_text("not a pdf")
    with pytest.raises(processing.ProcessingError):
        processing.validate_document(fake, ".pdf")


def test_pptx_structure_validation(tmp_path):
    valid = tmp_path / "valid.pptx"
    with zipfile.ZipFile(valid, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
    assert processing.validate_document(valid, ".pptx") is None
    invalid = tmp_path / "invalid.pptx"
    with zipfile.ZipFile(invalid, "w") as archive:
        archive.writestr("word/document.xml", "x")
    with pytest.raises(processing.ProcessingError):
        processing.validate_document(invalid, ".pptx")


def test_libreoffice_uses_argument_list_without_shell(tmp_path, monkeypatch):
    source = tmp_path / "source;touch injected.pptx"
    source.write_bytes(b"PK")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output = tmp_path / "source;touch injected.pdf"
        make_pdf(output, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(processing.subprocess, "run", fake_run)
    output = processing.convert_pptx(source, tmp_path, 5)
    assert output.is_file()
    assert isinstance(captured["command"], list)
    assert "shell" not in captured["kwargs"]
    assert captured["command"][-1] == str(source)


def test_text_and_image_watermarks_create_pdf(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "output.pdf"
    make_pdf(source, 2)
    text = processing.make_text_watermark("نسخة خاصة\nPrivate", 36, "#245c48", 0.3, -30)
    image_stream = io.BytesIO()
    Image.new("RGBA", (80, 40), (220, 20, 20, 180)).save(image_stream, format="PNG")
    processing.create_watermarked_pdf(
        source,
        output,
        {0},
        text_payload=text,
        text_position="tiled",
        image_payload=image_stream.getvalue(),
        image_position="bottom-right",
        image_scale=20,
    )
    with fitz.open(output) as result:
        assert result.page_count == 2
        assert len(result[0].get_images(full=True)) > 0
        assert len(result[1].get_images(full=True)) == 0


def test_normalize_rejects_non_image(tmp_path):
    path = tmp_path / "logo.png"
    path.write_text("not an image")
    with pytest.raises(processing.ProcessingError):
        processing.normalize_image_watermark(path, 0.5)
