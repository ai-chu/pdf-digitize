#!/usr/bin/env python3
"""勘察 PDF：页数、文字层、扫描件原生分辨率。用法: probe.py <pdf>"""
import sys
import pymupdf


def main(path):
    pdf = pymupdf.open(path)
    n = pdf.page_count
    print(f"文件: {path}")
    print(f"页数: {n}")

    # 抽 5 页看文字层
    samples = sorted({0, n // 4, n // 2, 3 * n // 4, n - 1})
    text_chars = [len(pdf[p].get_text().strip()) for p in samples]
    has_text = sum(1 for c in text_chars if c > 50)
    kind = "电子版(有文字层)" if has_text >= 3 else ("混合" if has_text else "扫描件(无文字层)")
    print(f"类型: {kind}  抽样页字符数: {dict(zip([s+1 for s in samples], text_chars))}")

    # 扫描件：估算内嵌图原生 dpi
    for p in samples:
        page = pdf[p]
        imgs = page.get_images(full=True)
        if not imgs:
            continue
        info = pdf.extract_image(imgs[0][0])
        w, h = info["width"], info["height"]
        rw, rh = page.rect.width / 72, page.rect.height / 72  # inch
        # 内嵌图可能横放存储，取两种取向下的较合理值
        dpi = min(max(w / rw, h / rh), max(w / rh, h / rw))
        print(f"  第{p+1}页 内嵌图 {w}x{h} ≈{dpi:.0f}dpi 格式{info['ext']}")
        break
    print("建议: 先跑 2-5 页小样 → 人眼核对 → 再全量。")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: probe.py <pdf>")
    main(sys.argv[1])
