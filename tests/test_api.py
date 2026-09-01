from pathlib import Path

import pymupdf as fitz
from fastapi.testclient import TestClient

from app import main


def pdf_bytes(pages: int = 2) -> bytes:
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=400, height=600)
        page.insert_text((40, 50), f"Document page {index + 1}")
    payload = document.tobytes()
    document.close()
    return payload


def test_complete_pdf_job_lifecycle(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    monkeypatch.setattr(main, "JOBS_DIR", jobs)
    with TestClient(main.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"status": "healthy"}

        response = client.post("/api/jobs", files={"file": ("report.pdf", pdf_bytes(), "application/pdf")})
        assert response.status_code == 201, response.text
        job = response.json()
        assert job["page_count"] == 2
        assert client.get(job["preview_url"]).headers["content-type"] == "image/png"

        generated = client.post(
            f"/api/jobs/{job['id']}/generate",
            data={
                "watermark_text": "سري — Confidential",
                "font_size": "42",
                "opacity": "0.3",
                "rotation": "-25",
                "color": "#254d3d",
                "position": "center",
                "page_mode": "custom",
                "custom_pages": "1",
                "image_position": "center",
                "image_scale": "25",
                "image_opacity": "0.3",
            },
        )
        assert generated.status_code == 200, generated.text
        download = client.get(generated.json()["download_url"])
        assert download.status_code == 200
        assert download.content.startswith(b"%PDF-")
        with fitz.open(stream=download.content, filetype="pdf") as result:
            assert result.page_count == 2
            assert len(result[0].get_images(full=True)) > 0

        deleted = client.delete(f"/api/jobs/{job['id']}")
        assert deleted.status_code == 204
        assert client.get(job["preview_url"]).status_code == 404


def test_rejects_extension_and_spoofed_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    with TestClient(main.app) as client:
        extension = client.post("/api/jobs", files={"file": ("payload.exe", b"MZ", "application/octet-stream")})
        assert extension.status_code == 400
        spoofed = client.post("/api/jobs", files={"file": ("payload.pdf", b"not-pdf", "application/pdf")})
        assert spoofed.status_code == 400
        traversal = client.get("/api/jobs/not-a-uuid/preview")
        assert traversal.status_code == 404


def test_upload_limit_is_enforced(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 16)
    with TestClient(main.app) as client:
        response = client.post("/api/jobs", files={"file": ("large.pdf", b"%PDF-" + b"x" * 50, "application/pdf")})
        assert response.status_code in {400, 413}
