#!/usr/bin/env python3
"""块级精准修补：只针对 real_loss_pages.json 里的页，单页重跑后
仅把与原空块 bbox 重叠的块内容回填（不整页替换，避免与跨页合并占位重复）。

用法: heal_targeted.py <原始pdf> <采集输出目录根>
前置: 先跑 verify_empty.py 生成 <书名>/real_loss_pages.json
"""
import functools
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

print = functools.partial(print, flush=True)


def overlap(a, b):
    ax0, ay0, ax1, ay1 = a; bx0, by0, bx1, by1 = b
    ix = max(0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    area_a = max(1, (ax1 - ax0) * (ay1 - ay0))
    return inter / area_a


def main(pdf, outroot):
    pdf, outroot = Path(pdf), Path(outroot)
    book = pdf.stem
    ha = outroot / book / "hybrid_auto"
    loss_f = outroot / book / "real_loss_pages.json"
    if not loss_f.exists():
        sys.exit("缺 real_loss_pages.json，先跑 verify_empty.py")
    loss_pages = json.loads(loss_f.read_text())
    cl_path = ha / f"{book}_content_list.json"
    blocks = json.loads(cl_path.read_text(encoding="utf-8"))
    for b in blocks:
        b["page_idx"] = int(b["page_idx"])

    patched, unresolved = [], []
    with tempfile.TemporaryDirectory() as td:
        for pg in loss_pages:  # 1-based PDF 页
            p = pg - 1
            targets = [b for b in blocks if b["page_idx"] == p
                       and b.get("type") in ("text", "table", "list")
                       and not str(b.get("text", "")).strip()
                       and not str(b.get("table_body", "")).strip()
                       and not any(str(x).strip() for x in (b.get("list_items") or []))]
            if not targets:
                continue
            r = subprocess.run(["mineru", "-p", str(pdf), "-o", td, "-s", str(p),
                                "-e", str(p), "-b", "hybrid-engine", "--effort", "high"],
                               capture_output=True, text=True)
            sub_cl = Path(td) / book / "hybrid_auto" / f"{book}_content_list.json"
            if not sub_cl.exists():
                unresolved.append(pg); print(f"  p{pg} 重跑失败"); continue
            sub = json.loads(sub_cl.read_text(encoding="utf-8"))
            sub_img = Path(td) / book / "hybrid_auto" / "images"
            if sub_img.exists():
                for f in sub_img.iterdir():
                    shutil.copy2(f, ha / "images" / f.name)
            ok = 0
            for t in targets:
                cands = [s for s in sub
                         if str(s.get("text", "")).strip() or str(s.get("table_body", "")).strip()]
                cands = [(overlap([float(v) for v in t["bbox"]],
                                  [float(v) for v in s["bbox"]]), s) for s in cands]
                cands = [c for c in cands if c[0] > 0.3]
                if not cands:
                    continue
                cands.sort(key=lambda x: -x[0])
                merged_text = "\n".join(str(s.get("text", "")).strip()
                                        for _, s in cands if str(s.get("text", "")).strip())
                best = cands[0][1]
                if t.get("type") == "table" or best.get("type") == "table":
                    t["type"] = "table"
                    t["table_body"] = best.get("table_body", "")
                    t["table_caption"] = best.get("table_caption", [])
                    if not str(t["table_body"]).strip() and merged_text:
                        t["text"] = merged_text
                else:
                    t["text"] = merged_text
                if str(t.get("text", "")).strip() or str(t.get("table_body", "")).strip():
                    ok += 1
            (patched if ok else unresolved).append(pg)
            print(f"  p{pg} 回填 {ok}/{len(targets)} 块")
            shutil.rmtree(Path(td) / book, ignore_errors=True)

    shutil.copy2(cl_path, str(cl_path) + ".bak2")
    cl_path.write_text(json.dumps(blocks, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).parent))
    from heal import rebuild_md
    md_path = ha / f"{book}.md"
    shutil.copy2(md_path, str(md_path) + ".bak2")
    md_path.write_text(rebuild_md(blocks, book), encoding="utf-8")
    print(f"块级修补完成：成功 {len(patched)} 页 {patched}；未解决 {len(unresolved)} 页 {unresolved}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
