#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""体检：正文里的图片引用 与 attachments 目录 是否一一对应。

用法:
    python check_images.py --dir "<vault>/Resources/<公众号名>"
"""
import argparse
import pathlib
import re

IMG_MD = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
IMG_HTML = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.I)


def body_of(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            return text[end + 4:]
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="公众号笔记目录（含 attachments/）")
    args = ap.parse_args()

    D = pathlib.Path(args.dir)
    ATT = D / "attachments"
    files = [p for p in sorted(D.glob("*.md")) if p.name != "00-索引.md"]
    n_md = n_html = n_none = 0
    referenced = set()
    examples = []
    noimg = []
    for p in files:
        body = body_of(p.read_text(encoding="utf-8", errors="ignore"))
        md = IMG_MD.findall(body)
        html = IMG_HTML.findall(body)
        if md:
            n_md += 1
            referenced |= {m for m in md}
            if len(examples) < 3:
                examples.append((p.name, len(md), md[0]))
        elif html:
            n_html += 1
            if len(examples) < 3:
                examples.append((p.name, len(html), "HTML:" + html[0][:60]))
        else:
            n_none += 1
            noimg.append(p.name)

    print(f"共 {len(files)} 篇")
    print(f"  正文含 markdown 图片: {n_md} 篇")
    print(f"  正文含裸 <img> 标签  : {n_html} 篇  (应为 0)")
    print(f"  正文完全无图片      : {n_none} 篇")

    print("\n示例:")
    for name, cnt, first in examples:
        print(f"  {name}: {cnt} 张，首张 {first[:80]}")

    files_on_disk = {f.name for f in ATT.iterdir()} if ATT.exists() else set()
    print(f"\nattachments 实际文件: {len(files_on_disk)} 个")
    ref_names = {r.split("/")[-1].split("\\")[-1] for r in referenced}
    print(f"正文中引用的文件名  : {len(ref_names)} 个")
    orphan = files_on_disk - ref_names
    print(f"未被任何笔记引用    : {len(orphan)} 个")
    if orphan:
        print("  例:", list(sorted(orphan))[:5])
    missing = ref_names - files_on_disk
    print(f"引用了但磁盘上没有  : {len(missing)} 个  <- 必须为 0")
    if missing:
        print("  例:", list(sorted(missing))[:5])

    if noimg:
        print(f"\n无图笔记 {len(noimg)} 篇前 10 篇（需回查原文确认是否本来就无图）:")
        for n in noimg[:10]:
            print("   ", n)


if __name__ == "__main__":
    main()
