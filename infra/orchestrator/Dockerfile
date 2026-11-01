FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AWS_REGION_NAME=ap-southeast-2 \
    PATH="/usr/local/bin:${PATH}"

ARG KUBECTL_VERSION=v1.36.5
ARG KUBECONFORM_VERSION=v0.8.0
ARG CONFTEST_VERSION=0.71.1

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/* \
    && curl --fail --silent --show-error --location \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
        --output /usr/local/bin/kubectl \
    && curl --fail --silent --show-error --location \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" \
        --output /tmp/kubectl.sha256 \
    && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum --check \
    && chmod 0755 /usr/local/bin/kubectl \
    && rm /tmp/kubectl.sha256 \
    && curl --fail --silent --show-error --location \
        "https://github.com/yannh/kubeconform/releases/download/${KUBECONFORM_VERSION}/kubeconform-linux-amd64.tar.gz" \
        --output /tmp/kubeconform.tar.gz \
    && echo "9bc2bffbf71f261128533edaf912153948b7ff238f9a531ae6d34466ec287883  /tmp/kubeconform.tar.gz" | sha256sum --check \
    && tar -xzf /tmp/kubeconform.tar.gz -C /usr/local/bin kubeconform \
    && rm /tmp/kubeconform.tar.gz \
    && curl --fail --silent --show-error --location \
        "https://github.com/open-policy-agent/conftest/releases/download/v${CONFTEST_VERSION}/conftest_${CONFTEST_VERSION}_Linux_x86_64.tar.gz" \
        --output /tmp/conftest.tar.gz \
    && echo "c3f6b2a753bd56e377a1e51a95ae3e057f926f2e9139f7d5b5dfbbaf810f7985  /tmp/conftest.tar.gz" | sha256sum --check \
    && tar -xzf /tmp/conftest.tar.gz -C /usr/local/bin conftest \
    && rm /tmp/conftest.tar.gz

WORKDIR /app

COPY infra/orchestrator/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --requirement /app/requirements.txt \
    && pip install --no-cache-dir checkov==3.4.0

COPY infra/orchestrator/src/ /app/orchestrator/src/
COPY infra/orchestrator/prompts/ /app/orchestrator/prompts/
COPY infra/orchestrator/schemas/ /app/orchestrator/schemas/
COPY infra/validator/src/ /app/validator/src/
COPY infra/validator/policy/ /app/validator/policy/

ENV PYTHONPATH="/app/orchestrator/src:/app/validator/src" \
    HOME="/tmp"

USER 10001:10001

ENTRYPOINT ["python", "/app/orchestrator/src/worker_app.py"]
