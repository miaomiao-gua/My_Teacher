#!/usr/bin/env bash
# ============================================================
# My Teacher v5 后端 · Linux 安装脚本（Debian/Ubuntu 系）
# 用法：  bash install.sh            （或 chmod +x 后 ./install.sh）
# 前置：  Python 3.10+，可选 Node.js（终端 JS 执行用）
# 结果：  在项目下创建 .venv-linux 虚拟环境并安装全部依赖，
#         首次运行会从 .env.example 生成 .env（需手动填 API Key）。
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "==> My Teacher 后端 Linux 安装"
echo "    项目目录: ${ROOT_DIR}"

# ---------- 1. Python 检查 ----------
PYTHON="${PYTHON:-python3}"
if ! command -v "${PYTHON}" >/dev/null 2>&1; then
    echo "错误: 未找到 ${PYTHON}，请先安装："
    echo "      sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    exit 1
fi
if ! "${PYTHON}" -c 'import sys; assert sys.version_info >= (3, 10)' 2>/dev/null; then
    echo "错误: 需要 Python 3.10 及以上（当前: $("${PYTHON}" --version 2>&1)）"
    exit 1
fi
echo "    使用 Python: $("${PYTHON}" --version 2>&1)"

# ---------- 2. 可选组件检查（仅提示，不强制） ----------
if ! command -v node >/dev/null 2>&1; then
    echo "提示: 未安装 Node.js。聊天终端里的 JavaScript/JS 代码将无法执行，"
    echo "      其余功能不受影响。安装: sudo apt install -y nodejs npm"
fi
if ! command -v bash >/dev/null 2>&1; then
    echo "错误: 未找到 bash（终端 shell 执行需要）"
    exit 1
fi

# ---------- 3. 创建虚拟环境 ----------
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/.venv-linux}"
if [ ! -d "${VENV_DIR}" ]; then
    echo "==> 创建虚拟环境: ${VENV_DIR}"
    "${PYTHON}" -m venv "${VENV_DIR}"
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
pip install --quiet --upgrade pip

# ---------- 4. 安装依赖 ----------
echo "==> 安装后端依赖（requirements.txt）..."
pip install --quiet -r "${ROOT_DIR}/server/requirements.txt"
echo "==> 安装 gunicorn（Linux 生产服务器）..."
pip install --quiet gunicorn

# ---------- 5. 可选：OCR 支持（扫描版 PDF 识别） ----------
# 依赖较重（会安装 torch），默认跳过。需要时取消下面两行注释：
# 国内网络可在 pip 后追加镜像，如：-i https://pypi.tuna.tsinghua.edu.cn/simple
# echo "==> 安装 OCR 依赖（easyocr / PyMuPDF）..."
# pip install --quiet easyocr PyMuPDF Pillow numpy
# 首次 OCR 会自动下载模型到 ~/.EasyOCR/model；网络受限可用 ghfast.top 加速。

# ---------- 6. 生成 .env ----------
if [ ! -f "${ROOT_DIR}/.env" ]; then
    cp "${ROOT_DIR}/.env.example" "${ROOT_DIR}/.env"
    echo "==> 已从 .env.example 生成 .env"
    echo "    请编辑 ${ROOT_DIR}/.env 填写云端 API Key 等配置"
else
    echo "    .env 已存在，跳过"
fi

# ---------- 7. 数据文件权限加固 ----------
# users.json / config.json 含密码哈希与 API Key，仅允许属主读写
chmod 700 "${ROOT_DIR}/data" 2>/dev/null || true
chmod 600 "${ROOT_DIR}/data/users.json" 2>/dev/null || true
chmod 600 "${ROOT_DIR}/data/config.json" 2>/dev/null || true
chmod 600 "${ROOT_DIR}/.env" 2>/dev/null || true
find "${ROOT_DIR}/data" -type f -name '*.json' -exec chmod 600 {} + 2>/dev/null || true

echo ""
echo "安装完成。接下来："
echo "  1) 编辑 .env 填写 API Key"
echo "  2) 开发模式启动：  bash ${SCRIPT_DIR}/run.sh"
echo "  3) 生产部署：      参照 my-teacher.service（systemd）"
