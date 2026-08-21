#!/bin/bash
# PDF 高保真采集：MinerU hybrid-engine（effort=high，含图表解析）
# 用法: digitize.sh <PDF路径> <输出目录> [起始页] [结束页]   页码从 0 计
set -euo pipefail

PDF="${1:?用法: digitize.sh <PDF路径> <输出目录> [起始页] [结束页]}"
OUT="${2:?缺少输出目录}"
START="${3:-}"
END="${4:-}"

VENV="$HOME/tools/pdf2md/.venv"
if [ ! -x "$VENV/bin/mineru" ]; then
  echo "MinerU 未安装，先运行: bash ~/.claude/skills/pdf-digitize/scripts/setup.sh" >&2
  exit 1
fi
source "$VENV/bin/activate"
export MINERU_MODEL_SOURCE="${MINERU_MODEL_SOURCE:-modelscope}"

ARGS=(-p "$PDF" -o "$OUT" -b hybrid-engine --effort high)
[ -n "$START" ] && ARGS+=(-s "$START")
[ -n "$END" ] && ARGS+=(-e "$END")

echo "== 采集: $PDF"
echo "== 参数: ${ARGS[*]}"
time mineru "${ARGS[@]}"

echo ""
echo "== 产物 =="
find "$OUT" -maxdepth 3 \( -name "*.md" -o -name "*content_list.json" \) | sed 's/^/  /'
echo ""
echo "下一步质检: python3 ~/.claude/skills/pdf-digitize/scripts/qc_report.py \"$PDF\" \"$OUT\""
