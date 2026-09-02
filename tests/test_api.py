import io
import time
import zipfile
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


def wait_for_batch(client: TestClient, job_id: str, timeout: float = 10) -> tuple[dict, list[dict]]:
    deadline = time.monotonic() + timeout
    observed = []
    while time.monotonic() < deadline:
        response = client.get(f"/api/batch/jobs/{job_id}/status")
        assert response.status_code == 200, response.text
        status = response.json()
        observed.append(status)
        if status["state"] in {"ready", "failed"}:
            return status, observed
        time.sleep(0.02)
    raise AssertionError("Batch did not reach a terminal state")


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


def test_batch_template_and_complete_generic_batch_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    csv_payload = (
        "\ufeffwatermark_text,output_name\r\n"
        '"نسخة خاصة - محمد أحمد - رقم 10542","محمد أحمد"\r\n'
        '"سري - للاستخدام الداخلي فقط","نسخة داخلية"\r\n'
    ).encode("utf-8")
    with TestClient(main.app) as client:
        template = client.get("/api/batch/template")
        assert template.status_code == 200
        assert template.content.startswith(b"\xef\xbb\xbf")
        assert 'filename="watermark-studio-template.csv"' in template.headers["content-disposition"]

        created = client.post(
            "/api/batch/jobs",
            files=[
                ("files", ("Course Guide.pdf", pdf_bytes(2), "application/pdf")),
                ("files", ("Module 1.pdf", pdf_bytes(1), "application/pdf")),
                ("watermarks_csv", ("watermarks.csv", csv_payload, "text/csv")),
            ],
        )
        assert created.status_code == 201, created.text
        batch = created.json()
        assert batch["source_count"] == 2
        assert batch["watermark_count"] == 2
        assert batch["output_count"] == 4
        assert batch["watermarks"][0]["watermark_text"] == "نسخة خاصة - محمد أحمد - رقم 10542"

        generated = client.post(
            f"/api/batch/jobs/{batch['id']}/generate",
            data={"font_name": "noto-sans", "font_size": "36", "opacity": "0.2", "rotation": "-45", "color": "#1f2937", "position": "tiled", "page_mode": "all", "custom_pages": ""},
        )
        assert generated.status_code == 202, generated.text
        assert generated.json() == {
            "status": "queued",
            "job_id": batch["id"],
            "status_url": f"/api/batch/jobs/{batch['id']}/status",
        }
        final_status, _ = wait_for_batch(client, batch["id"])
        assert final_status["state"] == "ready", final_status
        assert final_status["generated_count"] == 4
        assert final_status["percent"] == 100
        downloaded = client.get(final_status["download_url"])
        assert downloaded.status_code == 200
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            names = archive.namelist()
            assert names == [
                "محمد أحمد/Course Guide.pdf",
                "محمد أحمد/Module 1.pdf",
                "نسخة داخلية/Course Guide.pdf",
                "نسخة داخلية/Module 1.pdf",
                "_manifest.csv",
            ]
            assert archive.read("_manifest.csv").startswith(b"\xef\xbb\xbf")
            with fitz.open(stream=archive.read(names[0]), filetype="pdf") as result:
                assert result.page_count == 2
                assert len(result[0].get_images(full=True)) > 0

        assert client.delete(f"/api/jobs/{batch['id']}").status_code == 204


def test_batch_converts_each_pptx_once_before_all_watermarks(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    conversions = []

    def fake_convert(source, output_dir, timeout):
        conversions.append(source)
        output = output_dir / f"{source.stem}.pdf"
        output.write_bytes(pdf_bytes(1))
        return output

    monkeypatch.setattr(main, "convert_pptx", fake_convert)
    pptx = io.BytesIO()
    with zipfile.ZipFile(pptx, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
    csv_payload = b"watermark_text,output_name\nFirst,One\nSecond,Two\nThird,Three\n"
    with TestClient(main.app) as client:
        created = client.post(
            "/api/batch/jobs",
            files=[
                ("files", ("Deck.pptx", pptx.getvalue(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")),
                ("watermarks_csv", ("watermarks.csv", csv_payload, "text/csv")),
            ],
        )
        assert created.status_code == 201, created.text
        batch = created.json()
        assert len(conversions) == 1
        generated = client.post(
            f"/api/batch/jobs/{batch['id']}/generate",
            data={"position": "center", "page_mode": "all"},
        )
        assert generated.status_code == 202, generated.text
        final_status, _ = wait_for_batch(client, batch["id"])
        assert final_status["state"] == "ready", final_status
        assert final_status["generated_count"] == 3
        assert len(conversions) == 1
