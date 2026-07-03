FROM node:22-bookworm-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    NODE_PATH="/app/node_modules"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY requirements.txt pyproject.toml README.md ./
COPY rednote2tg ./rednote2tg

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir .

CMD ["rednote2tg"]
