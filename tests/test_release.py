import io
import time
import zipfile
from pathlib import Path

import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main, processing


def pdf_bytes(pages: int = 2, width: int = 420) -> bytes:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=width + index * 10, height=600)
        page.insert_text((40, 50), f"Reference page {index + 1}")
    payload = document.tobytes()
    document.close()
    return payload


def create_review_batch(client: TestClient) -> dict:
    csv_payload = (
        "watermark_text,output_name\n"
        '"محمد أحمد","محمد أحمد"\n'
        '"Abdullah Yaseen Abdullah Abualkheer ACA - 2026 - 001","Abdullah Yaseen"\n'
        '"نسخة خاصة - Abdullah Alharbi - ACA 2026\n' + "نص طويل\n" * 24 + '","Mixed Review"\n'
    ).encode("utf-8")
    response = client.post(
        "/api/batch/jobs",
        files=[
            ("files", ("التخطيط_sanitized.pdf", pdf_bytes(2), "application/pdf")),
            ("files", ("Long English Training Document.pdf", pdf_bytes(3, 500), "application/pdf")),
            ("watermarks_csv", ("watermarks.csv", csv_payload, "text/csv")),
        ],
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize(
    "text",
    [
        "محمد أحمد",
        "Abdullah Yaseen Abdullah Abualkheer ACA - 2026 - 001",
        "نسخة خاصة - Abdullah Alharbi - ACA 2026",
        "عبدالله الحربي\nAbdullah Alharbi\nACA - 2026 - 001",
        "ACA-2026-001 / ABC_123",
    ],
)
def test_glyph_aware_fallback_covers_supported_arabic_latin_and_mixed(text):
    check = processing.glyph_preflight(text, "noto-naskh")
    assert check["supported"] is True
    assert check["missing_characters"] == []
    payload = processing.make_text_watermark(text, 34, "#1f2937", 0.25, -35, "noto-naskh")
    with Image.open(io.BytesIO(payload)) as image:
        assert image.getbbox() is not None


def test_glyph_preflight_reports_an_unsupported_character():
    check = processing.glyph_preflight("Supported text 🧪", "noto-sans")
    assert check["supported"] is False
    assert "🧪" in check["missing_characters"]


def test_exact_preview_uses_final_pipeline_cache_and_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    calls = []
    original = processing.create_watermarked_pdf

    def tracked_final_pipeline(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(processing, "create_watermarked_pdf", tracked_final_pipeline)
    with TestClient(main.app) as client:
        batch = create_review_batch(client)
        endpoint = f"/api/batch/jobs/{batch['id']}/preview"
        base = {"source_index": "0", "watermark_index": "0", "page_number": "1", "position": "center", "rotation": "-35", "opacity": "0.2"}
        first = client.post(endpoint, data=base)
        assert first.status_code == 200, first.text
        assert first.headers["x-preview-cache"] == "MISS"
        assert first.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(first.content)) as image:
            assert image.width > 400 and image.height > 500
        assert len(calls) == 1

        cached = client.post(endpoint, data=base)
        assert cached.headers["x-preview-cache"] == "HIT"
        assert cached.headers["x-preview-key"] == first.headers["x-preview-key"]
        assert len(calls) == 1

        variants = [
            {**base, "rotation": "20", "opacity": "0.5"},
            {**base, "watermark_index": "1", "position": "tiled"},
            {**base, "watermark_index": "2"},
            {**base, "source_index": "1"},
            {**base, "source_index": "1", "page_number": "2"},
        ]
        keys = {first.headers["x-preview-key"]}
        for variant in variants:
            response = client.post(endpoint, data=variant)
            assert response.status_code == 200, response.text
            assert response.headers["x-preview-cache"] == "MISS"
            keys.add(response.headers["x-preview-key"])
        assert len(keys) == 6
        assert len(calls) == 6
        assert len(list((tmp_path / "jobs" / batch["id"] / "preview-cache").glob("*.png"))) == 6


def test_preflight_maps_missing_glyph_error_to_the_correct_row(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    with TestClient(main.app) as client:
        response = client.post(
            "/api/batch/jobs",
            files=[
                ("files", ("Reference.pdf", pdf_bytes(1), "application/pdf")),
                ("watermarks_csv", ("watermarks.csv", "watermark_text,output_name\nReady,Ready\nUnsupported 🧪,Check\n".encode("utf-8"), "text/csv")),
            ],
        )
        batch = response.json()
        result = client.post(f"/api/batch/jobs/{batch['id']}/preflight").json()
        assert result["summary"]["error"] == 1
        assert result["items"][1]["index"] == 1
        assert result["items"][1]["state"] == "error"
        assert "خط" in " ".join(result["items"][1]["messages"])


def test_preflight_review_persistence_and_strict_export(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    with TestClient(main.app) as client:
        batch = create_review_batch(client)
        preflight = client.post(
            f"/api/batch/jobs/{batch['id']}/preflight",
            data={"font_name": "noto-sans", "font_size": "48", "opacity": "0.2", "rotation": "-45", "position": "tiled", "filename_mode": "source"},
        )
        assert preflight.status_code == 200, preflight.text
        result = preflight.json()
        assert result["summary"]["error"] == 0
        assert result["items"][2]["state"] == "warning"
        assert "النص طويل" in " ".join(result["items"][2]["messages"])
        assert result["items"][2]["index"] == 2

        reviewed = client.post(f"/api/batch/jobs/{batch['id']}/review/1", data={"reviewed": "true"})
        assert reviewed.json() == {"reviewed": [1], "reviewed_count": 1, "total": 3}
        persisted = client.get(f"/api/batch/jobs/{batch['id']}/review")
        assert persisted.json()["reviewed"] == [1]
        assert (tmp_path / "jobs" / batch["id"] / "review.json").is_file()

        blocked = client.post(f"/api/batch/jobs/{batch['id']}/generate", data={"strict_review": "true"})
        assert blocked.status_code == 400
        assert "اعتماد جميع العلامات" in blocked.json()["detail"]
        for index in (0, 2):
            assert client.post(f"/api/batch/jobs/{batch['id']}/review/{index}", data={"reviewed": "true"}).status_code == 200
        queued = client.post(f"/api/batch/jobs/{batch['id']}/generate", data={"strict_review": "true"})
        assert queued.status_code == 202
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            status = client.get(f"/api/batch/jobs/{batch['id']}/status").json()
            if status["state"] in {"ready", "failed"}:
                break
            time.sleep(0.02)
        assert status["state"] == "ready", status


def test_strict_review_off_allows_unreviewed_export_and_filename_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    with TestClient(main.app) as client:
        response = client.post(
            "/api/batch/jobs",
            files=[
                ("files", ("Course_sanitized_sanitized.pdf", pdf_bytes(1), "application/pdf")),
                ("watermarks_csv", ("watermarks.csv", b"watermark_text,output_name\nConfidential,Person\n", "text/csv")),
            ],
        )
        batch = response.json()
        queued = client.post(
            f"/api/batch/jobs/{batch['id']}/generate",
            data={"strict_review": "false", "filename_mode": "source-and-output"},
        )
        assert queued.status_code == 202
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = client.get(f"/api/batch/jobs/{batch['id']}/status").json()
            if status["state"] == "ready":
                break
            time.sleep(0.02)
        downloaded = client.get(status["download_url"])
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert "Person/Course - Person.pdf" in archive.namelist()
            assert archive.testzip() is None
