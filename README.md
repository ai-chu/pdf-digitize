# pdf-digitize — PDF 书籍高保真采集 skill

把课标、教材、扫描书 PDF 转成**忠实副本**：结构化 Markdown＋表格＋切图＋页码索引＋质检报告。

## 为什么不用 macOS 自带 OCR

macOS Vision OCR 只输出文字流：表格行列关系全丢、结构图打散、美术字丢失、无图片提取、无结构标注（2026-08 用 400dpi 中文扫描书共 8 册实测确认）。它只够做全文检索索引，不够做内容副本。

## 引擎选型（2026-08 调研定版）

- **主路线：MinerU**（OpenDataLab 开源，AGPL）——OmniDocBench 本地解析器第一梯队，
  Apple Silicon 原生支持，hybrid-engine 精度约 95%，含表格/公式/图表解析、跨页表格合并。
  本地推理，数据不出机（涉隐私材料唯一允许路线）。
- 备选：mineru.net 官方 API（2000 页/日免费）；百度文档解析 API（PaddleOCR-VL，
  OmniDocBench 榜首，约 9 元/千页）作交叉验证第二意见。
- 不选：baidu/Unlimited-OCR、dots.ocr、DeepSeek-OCR 等——模型能力强但要求 NVIDIA CUDA，Mac 无法本地跑。

## 文件

| 文件 | 用途 |
|---|---|
| `SKILL.md` | Claude Code 工作流指令（勘察→采集→质检→归档） |
| `scripts/setup.sh` | 一次性环境安装（幂等） |
| `scripts/probe.py` | 勘察：页数/文字层/扫描原生 dpi |
| `scripts/digitize.sh` | 采集主命令 |
| `scripts/qc_report.py` | 质检报告＋抽样页渲染 |

## 装到另一台电脑

```bash
# 1. 拷贝本目录到目标机
scp -r ~/.claude/skills/pdf-digitize 目标机:~/.claude/skills/
# 2. 目标机上执行一次
bash ~/.claude/skills/pdf-digitize/scripts/setup.sh
```

要求：Apple Silicon Mac、macOS 14+、16GB 内存（推荐 32GB）、磁盘 ≥20GB（模型缓存 2-4GB）。
Intel Mac / 低内存机器：改用 mineru.net API 路线（SKILL.md 备选 A）。
