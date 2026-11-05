FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AWS_REGION_NAME=ap-southeast-2

WORKDIR /app
COPY infra/baseline/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --requirement /app/requirements.txt
COPY infra/baseline/src/ /app/
USER 10001:10001
CMD ["python", "/app/queue_worker.py"]
