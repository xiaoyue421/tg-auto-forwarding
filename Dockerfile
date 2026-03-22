ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM ${PYTHON_BASE_IMAGE}

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace/src \
    TG_QUEUE_DB_PATH=/data/tg_forwarder_queue.sqlite3

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt

RUN python -m pip install --upgrade pip setuptools wheel \
    && if [ -n "$PIP_INDEX_URL" ]; then \
        if [ -n "$PIP_TRUSTED_HOST" ]; then \
            python -m pip install --index-url "$PIP_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" -r requirements.txt; \
        else \
            python -m pip install --index-url "$PIP_INDEX_URL" -r requirements.txt; \
        fi; \
    else \
        python -m pip install -r requirements.txt; \
    fi

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-deps -e .

RUN mkdir -p /data

EXPOSE 8080

CMD ["python", "-m", "tg_forwarder", "web", "--config", "/workspace/.env", "--host", "0.0.0.0", "--port", "8080"]
