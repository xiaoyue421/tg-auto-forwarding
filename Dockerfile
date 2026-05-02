ARG PYTHON_BASE_IMAGE=python:3.12-slim
FROM node:20-bookworm-slim AS dashboard

WORKDIR /build
COPY src ./src
COPY frontend/package.json ./frontend/
WORKDIR /build/frontend
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_BASE_IMAGE}

ARG PIP_INDEX_URL=
ARG PIP_TRUSTED_HOST=

ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PIP_DEFAULT_TIMEOUT=300 \
    PYTHONPATH=/workspace/src \
    TG_QUEUE_DB_PATH=/data/tg_forwarder_queue.sqlite3

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
# 非 Premium 网页签到依赖仓库根目录 hdhive/（与 PYTHONPATH=/workspace/src 下的 tg_forwarder 配套）
COPY hdhive ./hdhive
COPY --from=dashboard /build/src ./src
COPY --from=dashboard /build/src/tg_forwarder/web/static /opt/tg-forwarder-dashboard-static
COPY docker/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Windows 检出 CRLF 时 shebang 会变成 /bin/sh\r，exec 会报 no such file or directory
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

# 不在此步升级 pip：升级会重新下载整包 pip wheel，国际链路易 Broken pipe。
# 基础镜像自带 pip 即可；仅对项目做 editable 安装，并加长超时与多轮退避重试。
# 国内构建可在 compose / build-arg 中设置 PIP_INDEX_URL、PIP_TRUSTED_HOST。
RUN set -eux; \
    _pip_install_editable() { \
        if [ -n "${PIP_INDEX_URL:-}" ]; then \
            if [ -n "${PIP_TRUSTED_HOST:-}" ]; then \
                python -m pip install --retries 15 --timeout 300 \
                    --index-url "$PIP_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" -e .; \
            else \
                python -m pip install --retries 15 --timeout 300 --index-url "$PIP_INDEX_URL" -e .; \
            fi; \
        else \
            python -m pip install --retries 15 --timeout 300 -e .; \
        fi; \
    }; \
    _pip_install_editable || (echo "pip -e . retry 1/4..."; sleep 25; _pip_install_editable) \
        || (echo "pip -e . retry 2/4..."; sleep 45; _pip_install_editable) \
        || (echo "pip -e . retry 3/4..."; sleep 60; _pip_install_editable) \
        || (echo "pip -e . retry 4/4..."; sleep 90; _pip_install_editable)

RUN mkdir -p /data

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["python", "-m", "tg_forwarder", "web", "--config", "/workspace/.env", "--host", "0.0.0.0", "--port", "8080"]
