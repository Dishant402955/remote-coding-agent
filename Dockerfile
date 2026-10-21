FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REMOTE_CODING_AGENT_HOST=0.0.0.0 \
    REMOTE_CODING_AGENT_PORT=8000 \
    REMOTE_CODING_AGENT_WORKSPACE=/workspace

WORKDIR /opt/remote-coding-agent

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir --no-compile . \
    && groupadd --system --gid 10001 agent \
    && useradd --system --uid 10001 --gid agent --create-home \
       --home-dir /home/agent --shell /usr/sbin/nologin agent \
    && mkdir -p /workspace \
    && chown agent:agent /workspace

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import socket; s=socket.create_connection(('127.0.0.1', 8000), timeout=2); s.close()"]

CMD ["remote-coding-agent"]
