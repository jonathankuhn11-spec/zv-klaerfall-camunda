# Worker-Image: Python-Paket + Prozessmodelle. Deployt beim Start die Modelle und startet die Worker.
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY bpmn ./bpmn
COPY forms ./forms
COPY samples ./samples
RUN pip install --no-cache-dir -e . && mkdir -p /data
ENV ZV_DATA_DIR=/data ZV_PROJEKT_DIR=/app CAMUNDA_URL=http://orchestration:8080 PYTHONUNBUFFERED=1
CMD ["zv", "worker"]
