#!/bin/bash
# pdf-digitize 环境安装（幂等，可重复执行）
# 用途：在 ~/tools/pdf2md/.venv 里安装 MinerU（PDF→Markdown 高保真采集引擎）
# 迁移到新电脑：拷贝 ~/.claude/skills/pdf-digitize/ 后运行本脚本一次即可
set -euo pipefail

VENV_DIR="$HOME/tools/pdf2md/.venv"
PY=""

echo "== pdf-digitize 环境安装 =="

# 1. 找 Python 3.10-3.13（MinerU 要求；系统自带 3.9 不够）
for cand in python3.12 python3.13 python3.11 python3.10; do
  for prefix in /opt/homebrew/bin /usr/local/bin; do
    if [ -x "$prefix/$cand" ]; then PY="$prefix/$cand"; break 2; fi
  done
done
if [ -z "$PY" ]; then
  echo "未找到 Python 3.10-3.13。请先安装：brew install python@3.12" >&2
  exit 1
fi
echo "Python: $PY ($($PY --version))"

# 2. 找 uv（比 pip 快得多）；没有则装到用户目录
UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  echo "安装 uv ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$HOME/.local/bin/uv"
fi

# 3. 建 venv 并安装 MinerU
mkdir -p "$(dirname "$VENV_DIR")"
if [ ! -d "$VENV_DIR" ]; then
  "$UV" venv --python "$PY" "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
# mlx 扩展：Apple 芯片 MLX 加速（默认引擎 vlm-engine，约快 3 倍）
# 注意：mlx-vlm 依赖 transformers>=5.1，与 hybrid-engine 互斥
"$UV" pip install -U "mineru[core,mlx]" pymupdf sentence-transformers lancedb

# 4. 国内网络默认走 ModelScope 下载模型（首跑自动下载约 2-4GB）
echo ""
echo "== 安装完成 =="
echo "venv: $VENV_DIR"
mineru --version
echo ""
echo "提示：首次解析会自动从 ModelScope 下载模型（约 2-4GB，缓存在 ~/.cache/modelscope）。"
echo "之后离线可用。采集命令见同目录 digitize.sh。"
