#!/usr/bin/env python3
"""云端交叉验证：把可疑页发给第二个引擎复核，与 MinerU 结果比对，
两引擎一致的页自动通过，分歧页列出来供人眼终审。

用法: crosscheck.py <原始pdf> <采集输出目录> [--pages 86,107] [--provider paddle|qwen] [--start 起始页0计]
不传 --pages 时自动读输出目录下的 risk_pages.json。

密钥（按顺序查找环境变量 → ~/.config/pdf-digitize/env）：
  paddle: BAIDU_OCR_AK / BAIDU_OCR_SK（百度智能云·文档解析 PaddleOCR-VL，约9元/千页）
  qwen:   DASHSCOPE_API_KEY（阿里百炼 qwen-vl-ocr）
隐私红线：涉隐私/敏感材料禁用本脚本（页面会上传云端）。
"""
import argparse
import base64
import difflib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pymupdf

CONF = Path.home() / ".config/pdf-digitize/env"


def load_env():
    if CONF.exists():
        for line in CONF.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def http_json(url, data=None, headers=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def render_page_b64(pdf, page_idx, dpi=200):
    pix = pdf[page_idx].get_pixmap(dpi=dpi)
    return base64.b64encode(pix.tobytes("png")).decode()


# ---------- 引擎 A：百度 PaddleOCR-VL ----------
def paddle_token():
    ak, sk = os.environ.get("BAIDU_OCR_AK"), os.environ.get("BAIDU_OCR_SK")
    if not (ak and sk):
        sys.exit("缺 BAIDU_OCR_AK / BAIDU_OCR_SK（写入 ~/.config/pdf-digitize/env）。"
                 "申请：百度智能云控制台 → 文字识别 → 文档解析（PaddleOCR-VL）")
    r = http_json("https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials"
                  f"&client_id={ak}&client_secret={sk}", data=b"")
    return r["access_token"]


def paddle_parse(token, png_b64, name):
    base = "https://aip.baidubce.com/rest/2.0/brain/online/v2/paddle-vl-parser"
    body = urllib.parse.urlencode({"file_data": png_b64, "file_name": name}).encode()
    hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    sub = http_json(f"{base}/task?access_token={token}", body, hdr)
    task_id = (sub.get("result") or {}).get("task_id") or sub.get("task_id")
    if not task_id:
        sys.exit(f"提交失败: {json.dumps(sub, ensure_ascii=False)[:300]}")
    qbody = urllib.parse.urlencode({"task_id": task_id}).encode()
    for _ in range(60):
        time.sleep(3)
        q = http_json(f"{base}/task/query?access_token={token}", qbody, hdr)
        s = json.dumps(q, ensure_ascii=False)
        m = re.search(r'"markdown_url"\s*:\s*"([^"]+)"', s)
        if m:
            url = m.group(1).replace("\\/", "/")
            with urllib.request.urlopen(url, timeout=60) as r:
                return r.read().decode("utf-8")
        status = str(q)
        if "fail" in status.lower() or "error_code" in status:
            sys.exit(f"解析失败: {s[:300]}")
    sys.exit("轮询超时（180秒）")


# ---------- 引擎 B：阿里 qwen-vl-ocr ----------
def qwen_parse(png_b64):
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        sys.exit("缺 DASHSCOPE_API_KEY（写入 ~/.config/pdf-digitize/env 或环境变量）")
    body = json.dumps({
        "model": "qwen-vl-ocr-latest",
        "messages": [{"role": "user", "content": [
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
            {"type": "text",
             "text": "将这一页完整转写为 Markdown：正文按段落，表格用 Markdown 表格，"
                     "公式用 LaTeX。只输出内容本身。"},
        ]}],
    }).encode()
    r = http_json("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                  body, {"Content-Type": "application/json",
                         "Authorization": f"Bearer {key}"})
    return r["choices"][0]["message"]["content"]


# ---------- 比对 ----------
def normalize(t):
    t = re.sub(r"<[^>]+>|!\[[^\]]*\]\([^)]*\)|[#*|`$\\\s]", "", t)
    trans = str.maketrans("，。：；？！（）“”‘’、—", ",.:;?!()\"\"''-—"[:14])
    return t.translate(trans)


def mineru_page_text(outdir: Path, page_idx: int):
    cls = [p for p in sorted(outdir.rglob("*content_list.json")) if "_v2" not in p.name]
    blocks = json.loads(cls[0].read_text(encoding="utf-8"))
    parts = []
    for b in blocks:
        if int(b.get("page_idx", -1)) != page_idx:
            continue
        if b.get("type") in ("page_number", "footer", "header"):
            continue
        parts.append(str(b.get("text", "")))
        parts.extend(str(x) for x in (b.get("list_items") or []))
        if b.get("type") == "table":
            parts.append(str(b.get("table_body", "")))
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("outdir")
    ap.add_argument("--pages", default=None, help="PDF页码(1计)逗号分隔；默认读 risk_pages.json")
    ap.add_argument("--provider", default="paddle", choices=["paddle", "qwen"])
    ap.add_argument("--start", type=int, default=0, help="采集时的起始页(0计)")
    a = ap.parse_args()
    load_env()

    outdir = Path(a.outdir)
    pdf = pymupdf.open(a.pdf)
    if a.pages:
        pages = [int(x) for x in a.pages.split(",")]  # PDF页码 1 计
    else:
        rp = outdir / "risk_pages.json"
        if not rp.exists():
            sys.exit("无 --pages 且未找到 risk_pages.json（先跑 qc_report.py）")
        pages = [r["pdf_page"] for r in json.loads(rp.read_text(encoding="utf-8"))]
    if not pages:
        print("无可疑页，无需交叉验证。"); return

    token = paddle_token() if a.provider == "paddle" else None
    rows, disputes = [], []
    for pg in pages:
        b64 = render_page_b64(pdf, pg - 1)
        remote = (paddle_parse(token, b64, f"p{pg}.png") if a.provider == "paddle"
                  else qwen_parse(b64))
        local = mineru_page_text(outdir, pg - 1 - a.start)
        ratio = difflib.SequenceMatcher(None, normalize(local), normalize(remote)).ratio()
        ok = ratio >= 0.90
        rows.append((pg, ratio, ok))
        print(f"PDF第{pg}页  相似度 {ratio:.0%}  {'✅ 一致' if ok else '⚠️ 分歧'}")
        if not ok:
            d = outdir / "crosscheck_disputes"; d.mkdir(exist_ok=True)
            (d / f"p{pg}_mineru.txt").write_text(local, encoding="utf-8")
            (d / f"p{pg}_{a.provider}.md").write_text(remote, encoding="utf-8")
            disputes.append(pg)

    rep = outdir / "交叉验证报告.md"
    rep.write_text("\n".join([
        f"# 交叉验证报告（MinerU × {a.provider}）",
        "",
        f"- 验证 {len(pages)} 页；一致 {len(pages)-len(disputes)} 页（自动通过）；分歧 {len(disputes)} 页",
        *(f"- PDF第{pg}页：{r:.0%} {'✅' if ok else '⚠️ 见 crosscheck_disputes/'}"
          for pg, r, ok in rows),
        "",
        ("**需人眼终审的页**：" + ", ".join(map(str, disputes))
         + "（两版输出已存 crosscheck_disputes/，对照 qc_samples/ 原图裁决）")
        if disputes else "**全部一致，本册终审自动通过。**",
    ]), encoding="utf-8")
    print(f"\n报告: {rep}")
    if disputes:
        print(f"需人眼终审: PDF页 {disputes}")


if __name__ == "__main__":
    main()
