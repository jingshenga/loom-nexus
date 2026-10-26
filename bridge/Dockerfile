FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg: video poster frame extraction
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Dependencies come from pyproject.toml (single source of truth).
# The protocol client library arrives with M1.
COPY pyproject.toml ./
RUN pip install --no-cache-dir $(python -c "import tomllib; print(' '.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")

COPY runner ./runner
COPY models ./models

CMD ["python", "-m", "runner.main"]
