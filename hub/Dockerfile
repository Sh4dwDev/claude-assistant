FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Claude Code: the chat engine when you use your Claude subscription instead of an API key.
# Its state (sessions) lives on the data volume; updates come with each re-install.
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates bash git ripgrep \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://claude.ai/install.sh | bash
ENV PATH="/root/.local/bin:${PATH}" CLAUDE_CONFIG_DIR=/data/claude DISABLE_AUTOUPDATER=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
