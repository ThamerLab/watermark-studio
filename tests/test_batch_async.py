import io
import threading
import time
import zipfile

import pymupdf as fitz
from fastapi.testclient import TestClient

from app import main


def pdf_bytes(pages: int = 1) -> bytes:
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


def create_batch(client: TestClient, rows: bytes = b"watermark_text,output_name\nFirst,One\nSecond,Two\n") -> dict:
    response = client.post(
        "/api/batch/jobs",
        files=[
            ("files", ("First.pdf", pdf_bytes(1), "application/pdf")),
            ("files", ("Second.pdf", pdf_bytes(1), "application/pdf")),
            ("watermarks_csv", ("watermarks.csv", rows, "text/csv")),
        ],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_generate_returns_immediately_and_prevents_duplicate_workers(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")
    original_generate = main.generate_batch_archive
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_generate(*args, **kwargs):
        calls.append(args[0])
        started.set()
        if not release.wait(5):
            raise RuntimeError("test worker timed out")
        return original_generate(*args, **kwargs)

    monkeypatch.setattr(main, "generate_batch_archive", slow_generate)
    with TestClient(main.app) as client:
        batch = create_batch(client)
        started_at = time.monotonic()
        response = client.post(f"/api/batch/jobs/{batch['id']}/generate", data={"position": "center"})
        elapsed = time.monotonic() - started_at
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert elapsed < 0.5
        assert started.wait(1)

        processing = client.get(response.json()["status_url"])
        assert processing.status_code == 200
        assert processing.json()["state"] == "processing"
        assert client.get(f"/api/batch/jobs/{batch['id']}/download").status_code == 409

        duplicate = client.post(f"/api/batch/jobs/{batch['id']}/generate", data={"position": "center"})
        assert duplicate.status_code == 202
        assert duplicate.json()["state"] == "processing"
        assert len(calls) == 1

        release.set()
        final_status, _ = wait_for_batch(client, batch["id"])
        assert final_status["state"] == "ready", final_status
        assert final_status["current"] == final_status["total"] == 4
        downloaded = client.get(final_status["download_url"])
        assert downloaded.status_code == 200
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            assert len(archive.namelist()) == 4

        existing = client.post(f"/api/batch/jobs/{batch['id']}/generate", data={"position": "center"})
        assert existing.status_code == 202
        assert existing.json()["state"] == "ready"
        assert len(calls) == 1


def test_progress_callback_increments_after_each_pdf_and_then_zips(tmp_path):
    path = tmp_path / "batch"
    path.mkdir()
    first = path / "first.pdf"
    second = path / "second.pdf"
    first.write_bytes(pdf_bytes(1))
    second.write_bytes(pdf_bytes(1))
    metadata = {
        "sources": [
            {"filename": "First.pdf", "base_pdf": first.name, "page_count": 1},
            {"filename": "Second.pdf", "base_pdf": second.name, "page_count": 1},
        ],
        "watermarks": [
            {"watermark_text": "First mark", "output_name": "One"},
            {"watermark_text": "Second mark", "output_name": "Two"},
        ],
    }
    events = []
    generated = main.generate_batch_archive(
        path,
        metadata,
        font_name="noto-sans",
        font_size=24,
        opacity=0.2,
        rotation=-35,
        color="#1f2937",
        position="center",
        page_mode="all",
        custom_pages="",
        progress_callback=lambda current, total, stage: events.append((current, total, stage)),
    )
    assert generated == 4
    assert events == [
        (1, 4, "generating"),
        (2, 4, "generating"),
        (3, 4, "generating"),
        (4, 4, "generating"),
        (4, 4, "zipping"),
    ]
    with zipfile.ZipFile(path / "batch.zip") as archive:
        assert len(archive.namelist()) == 4


def test_failed_worker_persists_safe_error_and_never_exposes_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "JOBS_DIR", tmp_path / "jobs")

    def fail_generate(*_args, **_kwargs):
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(main, "generate_batch_archive", fail_generate)
    with TestClient(main.app) as client:
        batch = create_batch(client, b"watermark_text\nConfidential\n")
        response = client.post(f"/api/batch/jobs/{batch['id']}/generate")
        assert response.status_code == 202
        final_status, observed = wait_for_batch(client, batch["id"])
        assert observed
        assert final_status["state"] == "failed"
        assert final_status["error"] == "فشلت معالجة الدفعة. تحقق من الملفات ثم أعد المحاولة."
        assert "sensitive" not in str(final_status)
        assert "download_url" not in final_status
        assert not (tmp_path / "jobs" / batch["id"] / "batch.zip").exists()
        assert client.get(f"/api/batch/jobs/{batch['id']}/download").status_code == 409
