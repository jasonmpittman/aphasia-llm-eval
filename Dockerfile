FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system aphasia-eval \
    && useradd --system --gid aphasia-eval --create-home aphasia-eval

COPY requirements.txt pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m pip install --no-deps .

COPY config ./config
COPY tasks ./tasks
COPY gold_tasks ./gold_tasks
RUN mkdir -p /data \
    && chown aphasia-eval:aphasia-eval /data

USER aphasia-eval

EXPOSE 5000

CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "--call", "aphasia_eval:create_app"]
