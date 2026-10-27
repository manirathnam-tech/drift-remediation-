FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AWS_REGION=ap-southeast-2 \
    AWS_DEFAULT_REGION=ap-southeast-2 \
    CORPUS_PATH=/app/corpus.jsonl

WORKDIR /app

COPY infra/injector/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --requirement /app/requirements.txt

COPY infra/injector/src/injector.py /app/injector.py
COPY harness/corpus.jsonl /app/corpus.jsonl

USER 10001:10001

ENTRYPOINT ["python", "/app/injector.py"]
