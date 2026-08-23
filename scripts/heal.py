#!/usr/bin/env python3
"""空块修补：长批次采集中 VLM 偶发转写失败会留下空 text/table/list 块（bbox 存在、内容为空）。
本脚本检测空块页 → 按连续区间聚簇重跑 → 用重跑结果整页替换 → 重建 Markdown。

用法: heal.py <原始pdf> <采集输出目录根>   （目录根下应有 <书名>/hybrid_auto/）
产物: <书名>_content_list.json 原地修补（原件备份 .bak），<书名>.md 重建（原件备份 .bak）
"""
import json
import shutil
import subprocess
import functools
import sys
import tempfile
from pathlib import Path

print = functools.partial(print, flush=True)


def block_text(b):
    """块的全部文字内容：text / table_body / list_items 三处任一。"""
    parts = [str(b.get("text", "")), str(b.get("table_body", ""))]
    parts += [str(x) for x in (b.get("list_items") or [])]
    return "\n".join(p for p in parts if p.strip())


def empty_pages(blocks):
    pages = set()
    for b in blocks:
        if b.get("type") in ("text", "table", "list") and not block_text(b).strip():
            pages.add(int(b["page_idx"]))
    return sorted(pages)


def clusters(pages, gap=2):
    out, cur = [], [pages[0]]
    for p in pages[1:]:
        if p - cur[-1] <= gap:
            cur.append(p)
        else:
            out.append((cur[0], cur[-1])); cur = [p]
    out.append((cur[0], cur[-1]))
    return out


def rebuild_md(blocks, book_dir_name):
    lines = []
    for b in blocks:
        t = b.get("type")
        txt = str(b.get("text", "")).strip()
        if t in ("page_number", "footer", "header"):
            continue
        if t == "text":
            lvl = b.get("text_level")
            lines.append(("#" * int(lvl) + " " + txt) if lvl and txt else txt)
        elif t == "table":
            cap = "".join(b.get("table_caption") or [])
            body = str(b.get("table_body", "")).strip()
            foot = "\n".join(b.get("table_footnote") or [])
            lines.append("\n\n".join(x for x in (cap, body, foot) if x))
        elif t in ("image", "chart"):
            ip = b.get("img_path", "")
            cap = " ".join((b.get("image_caption") or []) + (b.get("chart_caption") or []))
            content = str(b.get("content", "")).strip()
            foot = "\n".join((b.get("image_footnote") or []) + (b.get("chart_footnote") or []))
            part = [f"![{cap}]({ip})" if ip else cap]
            if content:
                part.append(f"<details><summary>{t}: {cap or '内容转写'}</summary>\n\n{content}\n\n</details>")
            if foot:
                part.append(foot)
            lines.append("\n\n".join(x for x in part if x))
        elif t == "equation":
            lines.append(txt)
        elif t == "list":
            items = [str(x).strip() for x in (b.get("list_items") or []) if str(x).strip()]
            lines.append("\n".join(items) if items else txt)
        elif txt:
            lines.append(txt)
    return "\n\n".join(x for x in lines if x)


def main(pdf, outroot):
    pdf, outroot = Path(pdf), Path(outroot)
    book = pdf.stem
    ha = outroot / book / "hybrid_auto"
    cl_path = ha / f"{book}_content_list.json"
    blocks = json.loads(cl_path.read_text(encoding="utf-8"))
    for b in blocks:
        b["page_idx"] = int(b["page_idx"])

    bad = empty_pages(blocks)
    if not bad:
        print("无空块页，无需修补。"); return
    print(f"空块页 {len(bad)} 个（PDF页 {[p+1 for p in bad[:20]]}{'…' if len(bad)>20 else ''}），聚簇重跑…")

    healed = {}
    with tempfile.TemporaryDirectory() as td:
        for s, e in clusters(bad):
            r = subprocess.run(
                ["mineru", "-p", str(pdf), "-o", td, "-s", str(s), "-e", str(e),
                 "-b", "hybrid-engine", "--effort", "high"],
                capture_output=True, text=True)
            sub_cl = Path(td) / book / "hybrid_auto" / f"{book}_content_list.json"
            if not sub_cl.exists():
                print(f"  区间 {s+1}-{e+1} 重跑失败，跳过：{r.stderr[-200:]}"); continue
            sub = json.loads(sub_cl.read_text(encoding="utf-8"))
            # 图片资产并入主目录
            sub_img = Path(td) / book / "hybrid_auto" / "images"
            if sub_img.exists():
                for f in sub_img.iterdir():
                    shutil.copy2(f, ha / "images" / f.name)
            for b in sub:
                p = int(b["page_idx"]) + s
                b["page_idx"] = p
                healed.setdefault(p, []).append(b)
            done = [p+1 for p in range(s, e+1) if p in healed]
            print(f"  区间 PDF{s+1}-{e+1} ✓ 修补 {done}")
            shutil.rmtree(Path(td) / book, ignore_errors=True)

    # 只替换重跑后空块消失（或减少）的页
    fixed, still = [], []
    new_blocks = []
    by_page = {}
    for b in blocks:
        by_page.setdefault(b["page_idx"], []).append(b)
    for p in sorted(by_page):
        if p in healed:
            old_empty = empty_pages(by_page[p])
            new_empty = empty_pages(healed[p])
            if len(new_empty) < len(old_empty) or (not new_empty and old_empty):
                new_blocks.extend(healed[p]); fixed.append(p + 1); continue
            elif old_empty:
                still.append(p + 1)
        new_blocks.extend(by_page[p])

    shutil.copy2(cl_path, str(cl_path) + ".bak")
    cl_path.write_text(json.dumps(new_blocks, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path = ha / f"{book}.md"
    shutil.copy2(md_path, str(md_path) + ".bak")
    md_path.write_text(rebuild_md(new_blocks, book), encoding="utf-8")
    print(f"修补完成：{len(fixed)} 页替换（{fixed[:20]}{'…' if len(fixed)>20 else ''}）；"
          f"{len(still)} 页仍有空块（{still[:10]}），可再跑一轮或转人眼。")
    print(f"content_list 与 md 已更新（原件 .bak 备份）。")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
