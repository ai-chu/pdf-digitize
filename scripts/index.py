#!/usr/bin/env python3
"""三通道语料库·向量通道：按 content_list 块建本地向量索引（BGE-M3 ＋ LanceDB）。

天然优势：块即语义切片，每块自带印刷页坐标——检索结果直接引用到《书名》印刷页 NNN。

用法:
  index.py build <采集输出目录根>                # 索引根目录下所有书（增量：同名书重建）
  index.py query <采集输出目录根> "问题" [-k 5] [--book 书名]

依赖: sentence-transformers, lancedb（setup.sh 可选安装）。全程本地，数据不出机。
模型: BAAI/bge-m3（首次自动下载约 2.3GB，国内走 ModelScope：MINERU_MODEL_SOURCE=modelscope 时同源）。
"""
import argparse
import functools
import json
import os
import sys
from pathlib import Path

print = functools.partial(print, flush=True)

MODEL_ID = "BAAI/bge-m3"
SUBDIRS = ("hybrid_auto", "vlm", "cloud", "auto")
MIN_CHARS = 20          # 过短块并入前块
MAX_CHARS = 1200        # 过长块按句切


def load_model():
    from sentence_transformers import SentenceTransformer
    src = os.environ.get("MINERU_MODEL_SOURCE", "")
    path = MODEL_ID
    if src == "modelscope":
        try:
            from modelscope import snapshot_download
            path = snapshot_download(MODEL_ID)
        except Exception:
            pass
    return SentenceTransformer(path)


def block_text(b):
    parts = [str(b.get("text", "")), str(b.get("table_body", "")),
             str(b.get("content", ""))]
    parts += [str(x) for x in (b.get("list_items") or [])]
    return "\n".join(p for p in parts if p.strip()).strip()


def iter_chunks(book_dir: Path):
    """产出 (book, pdf_page, print_page, type, text)。短块并前、长块按句切。"""
    ha = next((book_dir / d for d in SUBDIRS if (book_dir / d).exists()), None)
    if not ha:
        return
    cls = [p for p in sorted(ha.glob("*content_list.json")) if "_v2" not in p.name]
    if not cls:
        return
    blocks = json.loads(cls[0].read_text(encoding="utf-8"))
    pmap = {int(b["page_idx"]): str(b.get("text", "")).strip().lstrip("0") or "0"
            for b in blocks if b.get("type") == "page_number"}
    book = book_dir.name
    buf, buf_meta = "", None
    for b in blocks:
        if b.get("type") in ("page_number", "footer", "header"):
            continue
        t = block_text(b)
        if not t:
            continue
        pg = int(b["page_idx"])
        meta = (book, pg + 1, pmap.get(pg, f"PDF{pg+1}"), b.get("type", "text"))
        if len(buf) + len(t) < MIN_CHARS or (buf and len(buf) < MIN_CHARS):
            buf += ("\n" + t if buf else t); buf_meta = buf_meta or meta
            if len(buf) < MIN_CHARS:
                continue
            t, meta, buf, buf_meta = buf, buf_meta, "", None
        elif buf:
            yield (*buf_meta, buf); buf, buf_meta = "", None
        while len(t) > MAX_CHARS:
            cut = max(t.rfind("。", 0, MAX_CHARS), t.rfind("\n", 0, MAX_CHARS), MAX_CHARS)
            yield (*meta, t[:cut])
            t = t[cut:].lstrip("。\n")
        if t:
            yield (*meta, t)
    if buf:
        yield (*buf_meta, buf)


def db_open(root: Path):
    import lancedb
    return lancedb.connect(str(root / "_index" / "lancedb"))


def build(root: Path):
    model = load_model()
    db = db_open(root)
    books = [d for d in sorted(root.iterdir())
             if d.is_dir() and any((d / s).exists() for s in SUBDIRS)]
    if not books:
        sys.exit(f"{root} 下未找到采集产物目录")
    rows_all = []
    for bd in books:
        rows = [{"book": bk, "pdf_page": pp, "print_page": pr, "type": ty, "text": tx}
                for bk, pp, pr, ty, tx in iter_chunks(bd)]
        print(f"{bd.name}: {len(rows)} 块")
        rows_all.extend(rows)
    print(f"嵌入 {len(rows_all)} 块（BGE-M3，本地）…")
    vecs = model.encode([r["text"] for r in rows_all], batch_size=32,
                        show_progress_bar=True, normalize_embeddings=True)
    for r, v in zip(rows_all, vecs):
        r["vector"] = v.tolist()
    if "corpus" in list(db.list_tables()):
        db.drop_table("corpus")
    db.create_table("corpus", rows_all)
    print(f"索引完成 → {root/'_index'/'lancedb'}（{len(rows_all)} 块，可整目录拷贝分发）")


def query(root: Path, q: str, k: int, book: str | None):
    model = load_model()
    db = db_open(root)
    tbl = db.open_table("corpus")
    vec = model.encode([q], normalize_embeddings=True)[0].tolist()
    s = tbl.search(vec).limit(k * 3 if book else k)
    hits = s.to_list()
    if book:
        hits = [h for h in hits if book in h["book"]][:k]
    for i, h in enumerate(hits[:k], 1):
        print(f"[{i}] 《{h['book']}》印刷页 {h['print_page']}（PDF{h['pdf_page']}，{h['type']}）"
              f" 距离{h.get('_distance', 0):.3f}")
        print("    " + h["text"][:120].replace("\n", " ") + ("…" if len(h["text"]) > 120 else ""))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("root")
    qp = sub.add_parser("query"); qp.add_argument("root"); qp.add_argument("q")
    qp.add_argument("-k", type=int, default=5); qp.add_argument("--book", default=None)
    a = ap.parse_args()
    if a.cmd == "build":
        build(Path(a.root))
    else:
        query(Path(a.root), a.q, a.k, a.book)
