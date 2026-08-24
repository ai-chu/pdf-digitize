#!/usr/bin/env python3
"""云端采集路由（双服务商可选）。整本 PDF 上传云端——**涉隐私/敏感材料禁用**。

用法: cloud_digitize.py <PDF路径> <输出目录根> [--provider mineru|baidu] [--lang ch]
产物: <输出目录根>/<书名>/cloud/ 下的 md ＋ content_list ＋ images（与本地路线同构，
      修补链/质检/索引脚本可直接续接）。

服务商（密钥都写 ~/.config/pdf-digitize/env）:
  mineru（默认）: 免费 2000 页/日高优额度。MINERU_API_TOKEN=...（mineru.net 注册）
                  单文件实测上限 200 页，超限自动切段合并。
  baidu:          付费兜底（约 9 元/千页，1000 页免费测试），mineru 额度用尽时切换。
                  BAIDU_OCR_AK=... / BAIDU_OCR_SK=...（百度智能云开通"文档解析 PaddleOCR-VL"）
                  单文件 ≤500 页/≤100MB，超限自动切段合并。
                  ⚠️ 百度通道按官方文档实现、待首次实跑验证字段。
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


# ---------- 服务商 B：百度 文档解析（PaddleOCR-VL） ----------
def baidu_token():
    import urllib.parse as up
    ak, sk = os.environ.get("BAIDU_OCR_AK"), os.environ.get("BAIDU_OCR_SK")
    if not (ak and sk):
        sys.exit("缺 BAIDU_OCR_AK / BAIDU_OCR_SK（百度智能云开通'文档解析 PaddleOCR-VL'后创建，"
                 "写入 ~/.config/pdf-digitize/env）")
    r = req_json("https://aip.baidubce.com/oauth/2.0/token?grant_type=client_credentials"
                 f"&client_id={up.quote(ak)}&client_secret={up.quote(sk)}", data={})
    return r["access_token"]


def baidu_parse_part(tok, part_pdf: Path):
    """提交单段 PDF → 轮询 → 返回 (markdown文本, 解析JSON)。"""
    import base64
    import urllib.parse as up
    base = "https://aip.baidubce.com/rest/2.0/brain/online/v2/paddle-vl-parser"
    body = up.urlencode({"file_data": base64.b64encode(part_pdf.read_bytes()).decode(),
                         "file_name": part_pdf.name}).encode()
    hdr = {"Content-Type": "application/x-www-form-urlencoded"}
    r = urllib.request.Request(f"{base}/task?access_token={tok}", body, hdr)
    with urllib.request.urlopen(r, timeout=600) as resp:
        sub = json.loads(resp.read())
    task_id = (sub.get("result") or {}).get("task_id") or sub.get("task_id")
    if not task_id:
        sys.exit(f"百度提交失败: {json.dumps(sub, ensure_ascii=False)[:300]}")
    qbody = up.urlencode({"task_id": task_id}).encode()
    for _ in range(240):
        time.sleep(10)
        r = urllib.request.Request(f"{base}/task/query?access_token={tok}", qbody, hdr)
        with urllib.request.urlopen(r, timeout=120) as resp:
            q = json.loads(resp.read())
        res = q.get("result") or q
        st = str(res.get("status", "")).lower()
        if "success" in st or res.get("markdown_url"):
            md = ""
            if res.get("markdown_url"):
                with urllib.request.urlopen(res["markdown_url"], timeout=300) as u:
                    md = u.read().decode("utf-8", "replace")
            pj = {}
            if res.get("parse_result_url"):
                with urllib.request.urlopen(res["parse_result_url"], timeout=300) as u:
                    pj = json.loads(u.read())
            return md, pj
        if "fail" in st or res.get("task_error"):
            sys.exit(f"百度解析失败: {json.dumps(res, ensure_ascii=False)[:300]}")
    sys.exit("百度轮询超时（40 分钟）")


def baidu_json_to_blocks(pj, page_offset):
    """把百度解析 JSON 尽力映射为 content_list 块结构（字段以首跑实测为准）。"""
    blocks = []
    for page in (pj.get("pages") or pj.get("result", {}).get("pages") or []):
        pidx = int(page.get("page_num", page.get("page_id", 1))) - 1 + page_offset
        for el in (page.get("layouts") or page.get("elements") or []):
            t = str(el.get("type", "text")).lower()
            b = {"page_idx": pidx, "bbox": el.get("position") or el.get("bbox") or [0, 0, 0, 0]}
            if "table" in t:
                b["type"] = "table"
                b["table_body"] = el.get("markdown") or el.get("html") or el.get("text", "")
            elif "image" in t or "figure" in t:
                b["type"] = "image"; b["img_path"] = el.get("image_url", "")
                b["content"] = el.get("text", "")
            else:
                b["type"] = "text"; b["text"] = el.get("text", "") or el.get("markdown", "")
            blocks.append(b)
    return blocks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf"); ap.add_argument("outroot")
    ap.add_argument("--provider", default="mineru", choices=["mineru", "baidu"])
    ap.add_argument("--lang", default="ch")
    a = ap.parse_args()
    load_env()

    import subprocess
    import tempfile
    import pymupdf

    pdf = Path(a.pdf); outroot = Path(a.outroot)
    book = pdf.stem
    src = pymupdf.open(pdf)
    n = src.page_count

    if a.provider == "baidu":
        print(f"⚠️ 云端路由（百度）：整本《{book}》（{n} 页）将上传百度智能云——确认非隐私材料。")
        tok = baidu_token()
        dest = outroot / book / "cloud"
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "images").mkdir(parents=True)
        merged_blocks, merged_md = [], []
        with tempfile.TemporaryDirectory() as td:
            step = 500
            for i, s0 in enumerate(range(0, n, step), 1):
                e0 = min(s0 + step, n) - 1
                if n <= step:
                    pp = pdf
                else:
                    pp = Path(td) / f"{book}.part{i}.pdf"
                    d = pymupdf.open(); d.insert_pdf(src, from_page=s0, to_page=e0)
                    d.save(pp); d.close()
                print(f"提交段 {s0+1}-{e0+1} 页 …")
                md, pj = baidu_parse_part(tok, pp)
                merged_md.append(md)
                merged_blocks.extend(baidu_json_to_blocks(pj, s0))
        (dest / f"{book}_content_list.json").write_text(
            json.dumps(merged_blocks, ensure_ascii=False, indent=1), encoding="utf-8")
        (dest / f"{book}.md").write_text("\n\n".join(merged_md), encoding="utf-8")
        print(f"完成 → {dest}（{len(merged_blocks)} 块；百度图片为 30 天有效外链，"
              "如需长存请另行下载 images）")
        return

    token = os.environ.get("MINERU_API_TOKEN")
    if not token:
        sys.exit("缺 MINERU_API_TOKEN。到 https://mineru.net 注册（免费）→ API 管理页创建 token，"
                 "写入 ~/.config/pdf-digitize/env；或改用 --provider baidu")
    print(f"⚠️ 云端路由：整本《{book}》（{n} 页）将上传 mineru.net——确认非隐私材料。")

    with tempfile.TemporaryDirectory() as td:
        # API 单文件实测上限 200 页（官方文档写 600，以实测为准）——超限自动切段
        parts = []  # (part_pdf_path, page_offset)
        if n <= 200:
            parts = [(pdf, 0)]
        else:
            for i, s0 in enumerate(range(0, n, 200), 1):
                e0 = min(s0 + 200, n) - 1
                pp = Path(td) / f"{book}.part{i}.pdf"
                d = pymupdf.open(); d.insert_pdf(src, from_page=s0, to_page=e0)
                d.save(pp); d.close()
                parts.append((pp, s0))
            print(f"超 200 页，切为 {len(parts)} 段同批上传")

        # 1. 一次申请全部上传链接
        sub = req_json(f"{API}/file-urls/batch", {
            "files": [{"name": p.name, "is_ocr": True, "data_id": f"{book}#{off}"}
                      for p, off in parts],
            "enable_formula": True, "enable_table": True,
            "language": a.lang, "model_version": "vlm",
        }, token)
        batch_id = sub["data"]["batch_id"]
        urls = sub["data"]["file_urls"]

        # 2. PUT 上传。必须用 curl：urllib 会自动附加 Content-Type 头，
        #    与 OSS 预签名 URL 的签名不符导致 403。失败再试直连（防代理劫持 OSS 域名）。
        for (p, off), u in zip(parts, urls):
            print(f"上传 {p.name}（{p.stat().st_size//1048576}MB）…")
            r = subprocess.run(["curl", "-sS", "-f", "-X", "PUT", "-T", str(p), u],
                               capture_output=True, text=True)
            if r.returncode != 0:
                r = subprocess.run(["curl", "--noproxy", "*", "-sS", "-f", "-X", "PUT",
                                    "-T", str(p), u], capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"上传失败 {p.name}: {r.stderr[-300:]}")
        print(f"已提交 {len(parts)} 段，batch_id={batch_id}，轮询中…")

        # 3. 轮询直到全部 done
        results = None
        for i in range(360):
            time.sleep(15)
            q = req_json(f"{API}/extract-results/batch/{batch_id}", token=token)
            rs = q["data"]["extract_result"] or []
            fails = [r for r in rs if r.get("state") == "failed"]
            if fails:
                sys.exit(f"云端解析失败: {fails[0].get('err_msg', fails[0])}")
            if rs and all(r.get("state") == "done" for r in rs):
                results = rs; break
            if i % 8 == 0:
                done = sum(1 for r in rs if r.get("state") == "done")
                print(f"  {done}/{len(parts)} 段完成")
        if not results:
            sys.exit("轮询超时（90 分钟）")

        # 4. 下载各段、按页偏移合并、归一化命名
        dest = outroot / book / "cloud"
        if dest.exists():
            shutil.rmtree(dest)
        (dest / "images").mkdir(parents=True)
        offset_of = {str(r_.get("data_id", "")): int(str(r_.get("data_id", "#0")).split("#")[-1])
                     for r_ in results}
        merged_blocks, merged_md = [], []
        for r_ in sorted(results, key=lambda x: offset_of.get(str(x.get("data_id")), 0)):
            off = offset_of.get(str(r_.get("data_id")), 0)
            print(f"下载段 offset={off} …")
            with urllib.request.urlopen(r_["full_zip_url"], timeout=600) as resp:
                zf = zipfile.ZipFile(io.BytesIO(resp.read()))
            pd = Path(td) / f"part_{off}"
            zf.extractall(pd)
            cl = next((f for f in pd.rglob("*content_list.json") if "_v2" not in f.name), None)
            md = next((f for f in pd.rglob("*.md")), None)
            if not cl:
                sys.exit(f"段 offset={off} 结果包内未找到 content_list")
            blocks = json.loads(cl.read_text(encoding="utf-8"))
            for b in blocks:
                b["page_idx"] = int(b["page_idx"]) + off
            merged_blocks.extend(blocks)
            if md:
                merged_md.append(md.read_text(encoding="utf-8"))
            for im in pd.rglob("images/*"):
                shutil.copy2(im, dest / "images" / im.name)
        (dest / f"{book}_content_list.json").write_text(
            json.dumps(merged_blocks, ensure_ascii=False, indent=1), encoding="utf-8")
        (dest / f"{book}.md").write_text("\n\n".join(merged_md), encoding="utf-8")
    print(f"完成 → {dest}（{len(merged_blocks)} 块，覆盖 {n} 页）")
    print("续接: qc_report.py / heal.py / index.py 均可直接使用（自动识别 cloud/ 子目录）")


if __name__ == "__main__":
    main()
