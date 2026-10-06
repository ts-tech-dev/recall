FROM python:3.12-slim

# libglib2.0-0: needed by OpenCV (OCR); libgomp1: ONNX Runtime threading
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN grep -v -E '^(pytest|httpx)' requirements.txt > /tmp/req.txt \
 && pip install --no-cache-dir -r /tmp/req.txt \
 && rm /tmp/req.txt \
 # rapidocr pulls in the GUI OpenCV, which needs X11 libraries; keep only the headless build
 && pip uninstall -y opencv-python \
 && pip install --no-cache-dir --force-reinstall --no-deps opencv-python-headless
COPY recall ./recall

RUN useradd --uid 1000 --create-home recall \
 && mkdir -p /data /notes && chown recall:recall /data /notes
USER recall

# Settings, index, extracted images and the downloaded embedding model live in /data.
ENV RECALL_DATA_DIR=/data \
    RECALL_NOTES_DIR=/notes \
    PYTHONUNBUFFERED=1
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/status', timeout=4)"

CMD ["python", "-m", "recall", "--host", "0.0.0.0", "--port", "8765"]
