FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY ortobahn/ ortobahn/

RUN pip install --no-cache-dir -e ".[web]"

# Create data directory for SQLite
RUN mkdir -p /app/data

VOLUME ["/app/data"]

# Default: run the autonomous scheduler
CMD ["python", "-m", "ortobahn", "schedule", "--client", "vaultscaler"]
