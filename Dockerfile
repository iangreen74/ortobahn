FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml .
COPY ortobahn/ ortobahn/
RUN pip install --no-cache-dir ".[web]"

FROM python:3.12-slim

WORKDIR /app

# Install git + gh CLI (needed by CTO and CI-fix agents for self-deploying)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
       -o /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
       > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY pyproject.toml .
COPY ortobahn/ ortobahn/
RUN pip install --no-deps --no-cache-dir -e .

RUN useradd -r -s /bin/false ortobahn
USER ortobahn

EXPOSE 8000

CMD ["python", "-m", "ortobahn", "schedule", "--platforms", "bluesky"]
