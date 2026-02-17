FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY ortobahn/ ortobahn/

RUN pip install --no-cache-dir -e ".[web]"

# Default: run the autonomous scheduler
CMD ["python", "-m", "ortobahn", "schedule", "--client", "vaultscaler"]
