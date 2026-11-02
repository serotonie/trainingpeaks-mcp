FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/serotonie/trainingpeaks-mcp" \
      org.opencontainers.image.description="TrainingPeaks MCP over native Streamable HTTP" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    TP_LIVE_ENABLED=false

WORKDIR /app
COPY docker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .
COPY docker/server.py docker/test_server.py ./
RUN python -m unittest -v

USER 1000:1000
EXPOSE 3000
CMD ["python", "server.py"]
