#!/usr/bin/env python3
"""云端采集路由：mineru.net 官方 API（每日 2000 页免费高优额度，同引擎同产物格式）。
整本 PDF 上传云端解析——**涉隐私/敏感材料禁用**，只用于公开出版物。

用法: cloud_digitize.py <PDF路径> <输出目录根> [--lang ch]
产物: <输出目录根>/<书名>/cloud/ 下的 md ＋ content_list ＋ images（与本地路线同构，
      修补链/质检/索引脚本可直接续接）。

Token: 在 https://mineru.net 注册后于 API 管理页创建，写入 ~/.config/pdf-digitize/env：
  MINERU_API_TOKEN=eyJ...
限制: 单文件 ≤200MB 且 ≤600 页；超出请先用 pymupdf 拆分。
"""
import argparse
import functools
import io
import json
import os
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

print = functools.partial(print, flush=True)
API = "https://mineru.net/api/v4"
CONF = Path.home() / ".config/pdf-digitize/env"


def load_env():
    if CONF.exists():
        for line in CONF.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def req_json(url, data=None, token=None, method=None):
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, body, method=method or ("POST" if body else "GET"))
    r.add_header("Authorization", f"Bearer {token}")
    if body:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=120) as resp:
        out = json.loads(resp.read())
    if out.get("code") not in (0, 200, None):
        sys.exit(f"API 错误: {json.dumps(out, ensure_ascii=False)[:300]}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("outroot")
    ap.add_argument("--lang", default="ch")
    a = ap.parse_args()
    load_env()
    token = os.environ.get("MINERU_API_TOKEN")
    if not token:
        sys.exit("缺 MINERU_API_TOKEN。到 https://mineru.net 注册（免费）→ API 管理页创建 token，"
                 "写入 ~/.config/pdf-digitize/env")

    pdf = Path(a.pdf); outroot = Path(a.outroot)
    book = pdf.stem
    size_mb = pdf.stat().st_size / 1048576
    if size_mb > 200:
        sys.exit(f"文件 {size_mb:.0f}MB 超过 200MB 限制，请先拆分")
    print(f"⚠️ 云端路由：整本《{book}》将上传 mineru.net——确认非隐私材料。")

    # 1. 申请上传链接
    sub = req_json(f"{API}/file-urls/batch", {
        "files": [{"name": pdf.name, "is_ocr": True, "data_id": book}],
        "enable_formula": True, "enable_table": True,
        "language": a.lang, "model_version": "vlm",
    }, token)
    batch_id = sub["data"]["batch_id"]
    up_url = sub["data"]["file_urls"][0]

    # 2. PUT 上传（不设 Content-Type）
    print(f"上传 {size_mb:.0f}MB …")
    r = urllib.request.Request(up_url, pdf.read_bytes(), method="PUT")
    with urllib.request.urlopen(r, timeout=1800) as resp:
        resp.read()
    print(f"已提交，batch_id={batch_id}，轮询中…")

    # 3. 轮询
    zip_url = None
    for i in range(360):  # 最长 90 分钟
        time.sleep(15)
        q = req_json(f"{API}/extract-results/batch/{batch_id}", token=token)
        res = (q["data"]["extract_result"] or [{}])[0]
        st = res.get("state")
        if st == "done":
            zip_url = res["full_zip_url"]; break
        if st == "failed":
            sys.exit(f"云端解析失败: {res.get('err_msg', res)}")
        prog = res.get("extract_progress") or {}
        if i % 8 == 0:
            print(f"  {st} {prog.get('extracted_pages','?')}/{prog.get('total_pages','?')} 页")
    if not zip_url:
        sys.exit("轮询超时（90 分钟）")

    # 4. 下载解包并归一化命名（对齐本地产物约定）
    dest = outroot / book / "cloud"
    dest.mkdir(parents=True, exist_ok=True)
    print("下载结果包…")
    with urllib.request.urlopen(zip_url, timeout=600) as resp:
        zf = zipfile.ZipFile(io.BytesIO(resp.read()))
    zf.extractall(dest)
    for f in dest.rglob("*"):
        if f.is_file() and f.parent != dest and f.suffix in (".md", ".json"):
            shutil.move(str(f), dest / f.name)
    imgs = next((d for d in dest.rglob("images") if d.is_dir() and d.parent != dest), None)
    if imgs:
        shutil.move(str(imgs), dest / "images")
    for pat, target in ((r"*content_list.json", f"{book}_content_list.json"),
                        (r"*.md", f"{book}.md")):
        cands = [p for p in dest.glob(pat) if p.name != target and "QC" not in p.name]
        if cands and not (dest / target).exists():
            cands[0].rename(dest / target)
    print(f"完成 → {dest}")
    print(f"续接: qc_report.py / heal.py / index.py 均可直接使用（自动识别 cloud/ 子目录）")


if __name__ == "__main__":
    main()
