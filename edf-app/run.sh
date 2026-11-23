#!/usr/bin/env bash
# 一键启动：生成样例 -> 构建前端 -> 启动 FastAPI（同时托管 API 与前端）。
set -euo pipefail
cd "$(dirname "$0")/backend"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
.venv/bin/python -m scripts.generate_samples
if [ ! -d ../frontend/node_modules ]; then
  (cd ../frontend && npm install --cache ./.npm-cache)
fi
(cd ../frontend && npm run build)
exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
