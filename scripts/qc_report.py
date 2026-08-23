#!/usr/bin/env python3
"""采集质检：页覆盖、块统计、印刷页码映射、乱码启发式、抽样页渲染。
用法: qc_report.py <原始pdf> <采集输出目录> [起始页(0计,默认0)] [结束页(0计,默认末页)]
在输出目录下生成 QC报告.md 和 qc_samples/*.png（供人眼比对）。
注意：content_list.json 的 page_idx 相对于本次采集的起始页从 0 计。
"""
import json
import random
import re
import sys
from pathlib import Path

import pymupdf


def find_outputs(outdir: Path):
    cl = [p for p in sorted(outdir.rglob("*content_list.json")) if "_v2" not in p.name]
    md = [p for p in sorted(outdir.rglob("*.md")) if p.name != "QC报告.md"]
    return (cl[0] if cl else None), (md[0] if md else None)


def main(pdf_path, outdir, start=0, end=None):
    pdf_path, outdir = Path(pdf_path), Path(outdir)
    pdf = pymupdf.open(pdf_path)
    end = pdf.page_count - 1 if end is None else end
    n_range = end - start + 1

    cl_path, md_path = find_outputs(outdir)
    if not cl_path or not md_path:
        sys.exit(f"未找到采集产物（content_list.json / .md）于 {outdir}")

    blocks = json.loads(cl_path.read_text(encoding="utf-8"))
    for b in blocks:
        if "page_idx" in b:
            b["page_idx"] = int(b["page_idx"])

    pages_seen = {b["page_idx"] for b in blocks if "page_idx" in b}
    missing = [start + p + 1 for p in range(n_range) if p not in pages_seen]

    by_type = {}
    for b in blocks:
        by_type[b.get("type", "?")] = by_type.get(b.get("type", "?"), 0) + 1

    # 印刷页码映射（page_number 块）：PDF页 → 印刷页
    page_map = {}
    for b in blocks:
        if b.get("type") == "page_number" and str(b.get("text", "")).strip():
            page_map[start + b["page_idx"] + 1] = str(b["text"]).strip()

    md_text = md_path.read_text(encoding="utf-8")
    n_tables = md_text.count("<table") + len(re.findall(r"^\|.+\|$", md_text, re.M))
    n_imgs = len(re.findall(r"!\[[^\]]*\]\(", md_text))
    weird = len(re.findall(r"[-￰-￿]", md_text))
    cjk = len(re.findall(r"[一-鿿]", md_text))

    # 风险定位：按块类型与内容特征标记可疑页（终审只看这些页）
    PUA = re.compile("[\ue000-\uf8ff\ufff0-\uffff]")
    risk = {}  # page_idx -> set(reasons)
    page_text_len = {}
    for b in blocks:
        p = b.get("page_idx")
        if p is None:
            continue
        t, txt = b.get("type"), str(b.get("text", ""))
        page_text_len[p] = page_text_len.get(p, 0) + len(txt)
        if t == "table":
            risk.setdefault(p, set()).add("表格")
        elif t == "image":
            risk.setdefault(p, set()).add("图/结构图")
        if PUA.search(txt):
            risk.setdefault(p, set()).add("乱码嫌疑")
        if txt.count("$") >= 4:
            risk.setdefault(p, set()).add("公式密集")
    for p in [q for q in pages_seen if 2 < q < n_range - 3]:
        if page_text_len.get(p, 0) < 50:
            risk.setdefault(p, set()).add("内容异常少")

    risk_pages = [
        {"pdf_page": start + p + 1, "page_idx": p, "reasons": sorted(risk[p])}
        for p in sorted(risk)
    ]
    (outdir / "risk_pages.json").write_text(
        json.dumps(risk_pages, ensure_ascii=False, indent=1), encoding="utf-8")

    # 可疑页全部渲染（人眼/交叉验证共用），再随机补 3 页对照
    qc_dir = outdir / "qc_samples"
    qc_dir.mkdir(exist_ok=True)
    pool = sorted(pages_seen)
    random.seed(42)
    picks = list(dict.fromkeys(
        sorted(risk) + (random.sample(pool, min(3, len(pool))) if pool else [])
    ))
    for p in picks:
        pix = pdf[start + p].get_pixmap(dpi=150)
        pix.save(qc_dir / f"page_{start+p+1:04d}.png")

    report = outdir / "QC报告.md"
    map_lines = [f"  - PDF第{k}页 = 印刷页 {v}" for k, v in sorted(page_map.items())[:10]]
    lines = [
        f"# 采集质检报告：{pdf_path.name}",
        "",
        f"- 采集范围：PDF 第 {start+1}—{end+1} 页（共 {n_range} 页）；覆盖 {len(pages_seen)} 页",
        f"- **缺页（PDF页码）**：{missing if missing else '无'}",
        f"- 内容块统计：{json.dumps(by_type, ensure_ascii=False)}",
        f"- Markdown：{cjk} 个汉字；表格 {n_tables} 处；图片引用 {n_imgs} 处",
        f"- 乱码嫌疑字符（私用区）：{weird} 个" + ("　⚠️ 需排查" if weird > 20 else ""),
        f"- 印刷页码映射（前10条）：",
        *(map_lines or ["  - （未检出印刷页码）"]),
        "",
        f"## 可疑页清单（共 {len(risk_pages)} 页，终审只需看这些）",
        *(
            [f"- PDF第{r['pdf_page']}页：{('、'.join(r['reasons']))}" for r in risk_pages]
            or ["- 无可疑页"]
        ),
        "",
        "## 终审（三选一）",
        f"1. **云端交叉验证**（推荐大批量）：只发上述 {len(risk_pages)} 页，"
        f"约 {max(1, len(risk_pages)) * 0.009:.2f} 元（PaddleOCR-VL 9元/千页）。"
        "运行 crosscheck.py，两引擎一致的页自动通过，分歧页才需人眼。",
        f"2. **人眼核查**：只看 `qc_samples/` 里的可疑页原图，与 Markdown 对应段落比对。",
        "3. **跳过**：产物标注为「未终审草稿」。",
        "",
        "- [ ] 终审通过　方式：＿＿　核对人：＿＿　日期：＿＿",
        "- 引擎与参数：MinerU hybrid-engine --effort high",
    ]
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告: {report}")
    print(f"缺页: {missing if missing else '无'}  块统计: {by_type}")
    print(f"可疑页(PDF页码): {[r['pdf_page'] for r in risk_pages]}")
    print(f"渲染页: {[start+p+1 for p in picks]} → {qc_dir}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(
        sys.argv[1], sys.argv[2],
        int(sys.argv[3]) if len(sys.argv) > 3 else 0,
        int(sys.argv[4]) if len(sys.argv) > 4 else None,
    )
