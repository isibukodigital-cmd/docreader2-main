FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/home/docqa/.cache/huggingface

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt README.md ./
COPY src ./src
COPY scripts/setup_database.py ./scripts/setup_database.py
COPY supabase/migrations ./supabase/migrations
COPY pics ./pics
COPY profiles ./profiles

RUN python -m pip install --upgrade pip \
    && python -m pip install -e ".[api,embeddings,synth]"

RUN groupadd --gid 10001 docqa \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin docqa \
    && chown -R docqa:docqa /app

USER docqa

EXPOSE 8000

CMD ["uvicorn", "docqa.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
