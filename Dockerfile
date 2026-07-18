FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY config ./config
COPY src ./src
COPY scripts ./scripts

RUN pip install --no-cache-dir -e ".[web]" \
    && chmod +x scripts/*.sh

RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

COPY scripts/docker-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]