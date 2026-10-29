FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AWS_REGION_NAME=ap-southeast-2 \
    PORT=8080

WORKDIR /app

COPY infra/collector/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --requirement /app/requirements.txt

COPY infra/collector/src/ /app/

USER 10001:10001
EXPOSE 8080

CMD ["python", "/app/server.py"]
