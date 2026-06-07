#!/usr/bin/env bash
# Запуск сервиса Autonomica локально.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install --upgrade pip
  ./.venv/bin/pip install -r requirements.txt
fi

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Создан .env из .env.example — проверьте настройки (MODEL_NAME, VISION_ENABLED, INPUT_BACKEND)."
fi

exec ./.venv/bin/python src/server.py
