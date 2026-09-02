# Watermark Studio

Arabic-first, self-hosted PDF and PowerPoint watermarking. PowerPoint files are converted with headless LibreOffice, then PyMuPDF applies text and/or image watermarks to selected pages.

## Install on Proxmox VE

Run as root in the Proxmox VE shell:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ThamerLab/watermark-studio/main/install.sh)"
```

The installer creates a dedicated unprivileged Debian 13 LXC, installs Docker Engine and Compose, builds the app, verifies `/health`, and prints the URL. Defaults: 2 CPU cores, 4096 MB RAM, 12 GB disk, DHCP, port 8080.

It also installs these host-side helpers:

```bash
bash /root/watermark-studio-update.sh
bash /root/watermark-studio-uninstall.sh
```

Uninstall requires typing the exact container ID and refuses containers without the Watermark Studio marker.

## Local Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
curl http://127.0.0.1:8080/health
```

Documents live in a private Docker volume. Each job has a random UUID and is deleted after `FILE_RETENTION_MINUTES` (60 by default), or immediately from the UI. No Docker socket or external database is used.

Batch mode accepts multiple PDF/PPTX sources and a generic CSV with required `watermark_text` and optional `output_name` columns. Each PPTX is converted once, then every watermark row is applied to every base PDF. Glyph-aware font fallback covers Arabic, Latin, and mixed lines. On-demand exact previews reuse the final PDF pipeline and are cached inside the temporary job; preflight and review state are also stored there.

Generation runs in the background with disk-backed progress polling, and the download is exposed only after the ZIP is complete. ZIPs use Windows-friendly paths, source-only filenames by default, collision-safe names, and a UTF-8 BOM `_manifest.csv`. Download the Excel-compatible CSV template from the batch screen.

## Development tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The production installer must only be executed from a real Proxmox VE shell. CI and local tests do not create an LXC.
