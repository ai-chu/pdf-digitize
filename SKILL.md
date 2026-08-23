---
name: pdf-digitize
description: PDF 书籍/课标/教材高保真采集（忠实副本）。把扫描版或电子版 PDF 转成带表格、图片、结构的 Markdown＋资产目录，并出质检报告。当用户提到"OCR 这本书""采集课标/教材""PDF 转 Markdown""扫描件解析""建忠实副本"时使用。
---

# PDF 高保真采集（忠实副本）

目标产物不是"一串文字"，而是**可替代翻原书的结构化副本**：分级标题、Markdown/HTML 表格、切出的插图文件、阅读顺序正确的正文、页码映射、质检报告。

## 引擎与路线（2026-08 定版）

| 路线 | 何时用 | 说明 |
|---|---|---|
| **主路线：MinerU（本地）** | 默认，一切常规书籍 | `hybrid-engine --effort high`，Apple Silicon 本地推理，数据不出机。环境在 `~/tools/pdf2md/.venv`，没有就先跑 `scripts/setup.sh` |
| 备选 A：mineru.net 官方 API | 本机太慢/环境坏了 | 每日 2000 页免费高优额度，单文件 ≤600 页需拆分 |
| 备选 B：百度文档解析 API（PaddleOCR-VL） | 精度存疑需第二意见交叉验证 | OmniDocBench 榜首；约 9 元/千页，1000 页免费测试；异步接口 2QPS |
| 兜底：macOS Vision OCR（ocrbin） | 只要全文检索索引、不要结构 | 快且免费，但表格/图/结构全丢——**不得称为忠实副本** |

不要用：纯 Vision/Tesseract 冒充结构化采集；有 CUDA 依赖的方案（本机是 Mac）。

## 工作流（四步，一步不能省）

### 第 1 步：勘察（先看清 PDF 是什么）

```bash
source ~/tools/pdf2md/.venv/bin/activate
python3 ~/.claude/skills/pdf-digitize/scripts/probe.py <PDF路径>
```

看三件事：页数；有无文字层（born-digital 还是扫描件）；扫描件内嵌图的原生 dpi。
**先跑 2-5 页小样**（`digitize.sh` 传起止页），确认质量再放全量——一本书本地跑要几十分钟，不要盲跑。

### 第 2 步：采集

```bash
bash ~/.claude/skills/pdf-digitize/scripts/digitize.sh <PDF路径> <输出目录> [起始页] [结束页]
```

起止页从 0 计。全量采集省略页码参数。多本书排队跑就逐本调用（后台执行，别并行——内存会爆）。

### 第 2.5 步：空块修补链（全自动，整册采集后必跑）

```bash
python3 ~/.claude/skills/pdf-digitize/scripts/heal.py <PDF路径> <输出目录根>            # ① 粗修
python3 ~/.claude/skills/pdf-digitize/scripts/verify_empty.py <PDF路径> <输出目录根>    # ② 分类残留
python3 ~/.claude/skills/pdf-digitize/scripts/heal_targeted.py <PDF路径> <输出目录根>   # ③ 块级精修
```

**背景知识（2026-08-23 两册 473 页实测）**——content_list 里"空块"有三种，处置不同：
1. **偶发转写失败**（长批次 VLM 偶发返回空，约占页面 15%）→ ① 聚簇重跑可修
2. **跨页合并占位**（段落并入前页块，本页留空壳，**良性设计**）→ ② 用 qwen 裁片转写＋全书检索区分出来，不动
3. **真丢失残留** → ③ 单页重跑后只回填与空块 bbox 重叠的内容，避免与前页合并内容重复

**关键字段陷阱**：list 块的内容在 `list_items` 字段（不在 `text`）；chart/image 的转写在
`content` 字段。凡是读 content_list 的代码都必须三处齐查，否则会把完好内容误判为丢失。
修补链会重建 .md（含表格/图表转写/公式，原件 .bak 备份）；content_list 永远是权威数据源。

### 第 3 步：机器质检＋风险定位（全自动）

```bash
python3 ~/.claude/skills/pdf-digitize/scripts/qc_report.py <PDF路径> <输出目录> [起始页] [结束页]
```

起止页与采集时传的一致（content_list 的 page_idx 相对起始页计数，不传会误报缺页）。
产出 `QC报告.md`（缺页检查、块统计、PDF页↔印刷页映射）＋ `risk_pages.json`（**可疑页清单**：
表格页/图页/乱码嫌疑/公式密集/内容异常少）＋可疑页原图渲染。一本 250 页的书通常只有
20—40 个可疑页——终审只看这些，不用通读全书。

### 第 3.5 步：终审方式（**必须问用户，三选一，不得自动决定**）

拿到可疑页清单后，用提问工具让用户选（报出可疑页数和预估费用）：

| 选项 | 做法 | 适用 |
|---|---|---|
| **A 云端交叉验证**（大批量推荐） | `crosscheck.py` 把可疑页发给第二引擎复核，相似度 ≥90% 自动通过，分歧页才留给人眼 | 书多看不过来；材料可上云 |
| **B 人眼核查** | 只看 `qc_samples/` 里的可疑页原图 vs Markdown | 书少或材料敏感 |
| **C 跳过** | 产物标注「未终审草稿」 | 只做检索用途 |

必须问用户的原因：交叉验证要把书页**上传云端**（涉隐私/敏感材料是红线），且花的是用户的钱。

```bash
# 选项 A 的执行（默认读 risk_pages.json；--provider paddle 或 qwen）
python3 ~/.claude/skills/pdf-digitize/scripts/crosscheck.py <PDF路径> <输出目录> --provider qwen --start <采集起始页>
```

- `qwen`＝阿里 qwen-vl-ocr（读 `DASHSCOPE_API_KEY`，已实测可用，按 token 计费约几厘/页）
- `paddle`＝百度 PaddleOCR-VL（读 `BAIDU_OCR_AK/SK`，约 9 元/千页，OmniDocBench 榜首；首次使用需在百度智能云开通"文档解析"）
- 密钥放 `~/.config/pdf-digitize/env`（权限 600），格式 `KEY=value` 每行一条
- 产出 `交叉验证报告.md`：一致页自动通过；分歧页的两版输出存 `crosscheck_disputes/`，人眼只裁决这几页

### 第 4 步：定版归档

MinerU 实际产出结构：

```
<输出目录>/<书名>/hybrid_auto/
├── <书名>.md                  # 全文 Markdown（表格为 HTML 表含 rowspan/colspan，图为相对链接）
├── images/                    # 切出的插图/图表（含表格截图备份）
├── <书名>_content_list.json   # 逐块内容：type(text/table/image/page_number/footer…)＋page_idx
├── <书名>_middle.json         # 版面级中间数据（含坐标）
├── <书名>_layout.pdf          # 版面框可视化（人工复核用）
└── ../QC报告.md ＋ risk_pages.json ＋ qc_samples/   # qc_report.py 生成
    ../交叉验证报告.md ＋ crosscheck_disputes/        # 选云端终审时 crosscheck.py 生成
```

页眉/页脚/印刷页码被识别为独立块类型，不混入正文；印刷页码在 QC 报告中自动生成映射表。

归档时在 QC 报告头部写明：引擎与版本、backend/effort 参数、采集日期、终审方式与结论。

## 实测基准与已知限制（2026-08-20，Apple M5 / 32GB，400dpi 中文扫描书实测)

**能做到**：HTML 表格含 rowspan/colspan 精确还原（评价量表 4 维 11 行全对）；树状结构图自动转
mermaid（层级连线正确）；数学公式转 LaTeX；脚注转上标；段落重排（不按印刷行硬切）；页眉/页脚/
印刷页码剥离为独立块；扉页装饰识别为 text_image 折叠块；插图切出为独立文件。

**已知限制**：
- 美术体大字（如题号"问题 8"的艺术数字）进切图、不进标题文本——全文检索题号会漏，需要时从目录页或 content_list 的 image 块补
- mermaid 图是模型对结构图的**转述**，定版引用前需与切出的原图人眼核对一遍
- 速度约 20—30 秒/页（effort=high）：250 页一册约 1.5—2.5 小时，放后台跑，多本排队勿并行

## 红线

- **终审未过（交叉验证全一致 或 人眼核查通过 二者其一），不得对外宣称"忠实副本"**——只能叫「未终审草稿」
- **是否启动云端交叉验证必须由用户决定**：会上传书页且产生费用，不得替用户默认开启
- 涉隐私或未公开的敏感材料不得走任何云端 API 路线，只许本地 MinerU
- content_list.json 的 page_idx 是 **PDF 页码**，与书的印刷页码有偏移；引用原文时给两个页码
- 首次在新电脑运行前必跑 `setup.sh`；模型缓存约 2-4GB 在 `~/.cache/modelscope`

## 迁移到另一台电脑

1. 拷贝整个 `~/.claude/skills/pdf-digitize/` 到目标机同路径
2. 目标机跑一次 `bash ~/.claude/skills/pdf-digitize/scripts/setup.sh`
3. 首次采集自动下载模型（国内网络已默认 ModelScope 源）
