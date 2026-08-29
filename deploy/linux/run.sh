#!/usr/bin/env bash
# ============================================================
# My Teacher v5 后端 · Linux 启动脚本
#   MODE=dev  开发模式：python app.py（热重载模板，直接看报错）
#   MODE=prod 生产模式：gunicorn 多进程（默认 2 个 worker）
# 环境变量：
#   HOST / PORT / GUNICORN_WORKERS / MODE
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv-linux}"
if [ ! -d "${VENV_DIR}" ]; then
    echo "错误: 未找到虚拟环境 ${VENV_DIR}，请先运行 install.sh" >&2
    exit 1
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

export PYTHONPATH="${ROOT_DIR}/server${PYTHONPATH:+:${PYTHONPATH}}"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"
MODE="${MODE:-dev}"

cd "${ROOT_DIR}/server"

if [ "${MODE}" = "prod" ]; then
    WORKERS="${GUNICORN_WORKERS:-2}"
    echo "==> 生产模式启动 gunicorn（${WORKERS} workers）: ${HOST}:${PORT}"
    exec gunicorn -w "${WORKERS}" -b "${HOST}:${PORT}" --timeout 120 --graceful-timeout 30 app:app
else
    echo "==> 开发模式启动: http://${HOST}:${PORT}/  （生产部署请用 MODE=prod）"
    exec python app.py
fi
