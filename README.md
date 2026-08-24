# pdf-digitize — PDF → 三通道语料库 skill

把课标、教材、扫描书 PDF 转成**三通道语料库**：
①精确通道（结构化忠实副本：Markdown＋表格＋切图＋印刷页映射）
②向量通道（BGE-M3＋LanceDB 本地语义索引，命中直接引用到印刷页）
③结构通道（书→页→块层级 JSON）。附质检报告与自动修补链。全链本地免费。

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
| `scripts/qc_report.py` | 质检报告＋风险定位＋抽样页渲染 |
| `scripts/heal.py` / `verify_empty.py` / `heal_targeted.py` | 空块修补链（粗修→分类→块级精修） |
| `scripts/crosscheck.py` | 云端交叉验证（PaddleOCR-VL / qwen-vl-ocr 第二意见） |
| `scripts/index.py` | 向量索引 build/query（BGE-M3＋LanceDB，印刷页级引用） |

## 装到另一台电脑

```bash
# 1. 拷贝本目录到目标机
scp -r ~/.claude/skills/pdf-digitize 目标机:~/.claude/skills/
# 2. 目标机上执行一次
bash ~/.claude/skills/pdf-digitize/scripts/setup.sh
```

要求：Apple Silicon Mac、macOS 14+、16GB 内存（推荐 32GB）、磁盘 ≥20GB（模型缓存 2-4GB）。
Intel Mac / 低内存机器：改用 mineru.net API 路线（SKILL.md 备选 A）。
