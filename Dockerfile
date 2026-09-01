FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    HOME=/app/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       fontconfig \
       fonts-crosextra-caladea \
       fonts-crosextra-carlito \
       fonts-liberation2 \
       fonts-noto-core \
       libraqm0 \
       libreoffice-common \
       libreoffice-core \
       libreoffice-impress \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN groupadd --gid 10001 watermark \
    && useradd --uid 10001 --gid watermark --no-create-home --home-dir /app/data watermark \
    && mkdir -p /app/data/jobs \
    && chown -R watermark:watermark /app/data

USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=20s --timeout=5s --start-period=90s --retries=5 \
  CMD ["python", "-c", "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3))['status']=='healthy'"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
