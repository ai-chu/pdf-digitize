#!/usr/bin/env python3
"""空块分类器：区分「跨页合并占位（良性）」与「真丢失」。
对每个空 text/table/list 块：裁出 bbox 区域 → qwen 转写 → 在全书 md 中模糊搜索。
搜得到 → 良性占位；搜不到且区域有实质文字 → 真丢失（输出待修页清单 real_loss_pages.json）。

用法: verify_empty.py <原始pdf> <采集输出目录根>
需要 DASHSCOPE_API_KEY（环境变量或 ~/.config/pdf-digitize/env）。隐私材料禁用（区域上云）。
"""
import base64

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

CONF = Path.home() / ".config/pdf-digitize/env"
if CONF.exists():
    for line in CONF.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import pymupdf  # noqa: E402


def qwen_ocr(png_b64):
    body = json.dumps({
        "model": "qwen-vl-ocr-latest",
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
            {"type": "text", "text": "转写图中全部文字，无文字则只回复：无文字。"},
        ]}],
    }).encode()
    req = urllib.request.Request(
        "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", body,
        {"Content-Type": "application/json",
         "Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def norm(t):
    return re.sub(r"[\s，。：；、！？“”‘’()（）\[\]<>|#*$\\-—·]", "", t)


def main(pdf_path, outroot):
    pdf_path, outroot = Path(pdf_path), Path(outroot)
    book = pdf_path.stem
    ha = outroot / book / "hybrid_auto"
    blocks = json.loads((ha / f"{book}_content_list.json").read_text(encoding="utf-8"))
    md_norm = norm((ha / f"{book}.md").read_text(encoding="utf-8"))
    pdf = pymupdf.open(pdf_path)

    empties = [b for b in blocks if b.get("type") in ("text", "table", "list")
               and not str(b.get("text", "")).strip()
               and not str(b.get("table_body", "")).strip()
               and not any(str(x).strip() for x in (b.get("list_items") or []))]
    print(f"{book}: {len(empties)} 个空块待分类", flush=True)

    benign, loss, deco = [], [], []
    for i, b in enumerate(empties, 1):
        p = int(b["page_idx"])
        x0, y0, x1, y1 = [float(v) for v in b["bbox"]]
        page = pdf[p]
        rect = pymupdf.Rect(x0/1000*page.rect.width, y0/1000*page.rect.height,
                            x1/1000*page.rect.width, y1/1000*page.rect.height)
        pix = page.get_pixmap(dpi=200, clip=rect)
        try:
            txt = qwen_ocr(base64.b64encode(pix.tobytes("png")).decode()).strip()
        except Exception as e:
            print(f"  [{i}] p{p+1} 调用失败 {e}", flush=True); continue
        n = norm(txt)
        if txt.startswith("无文字") or len(n) < 12:
            deco.append(p + 1)
        elif n[:40] in md_norm or n[-40:] in md_norm or n[len(n)//2-15:len(n)//2+15] in md_norm:
            benign.append(p + 1)
        else:
            loss.append(p + 1)
            print(f"  [{i}] p{p+1} 真丢失 首30字: {txt[:30]}", flush=True)
        if i % 20 == 0:
            print(f"  …{i}/{len(empties)}", flush=True)

    loss = sorted(set(loss))
    (outroot / book / "real_loss_pages.json").write_text(
        json.dumps(loss, ensure_ascii=False), encoding="utf-8")
    print(f"分类完成：良性占位 {len(set(benign))} 页｜装饰无字 {len(set(deco))} 页｜"
          f"真丢失 {len(loss)} 页 {loss}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
