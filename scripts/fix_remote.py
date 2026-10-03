#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补救：把笔记正文里残留的远程图片下载到 attachments，并替换成本地相对路径。

出现残留说明抓取/替换环节有 URL 对不上（见 references/pitfalls.md 第 3 条）。
核心脚本已修，此脚本用于修历史遗留数据。

用法:
    python fix_remote.py --dir "<vault>/Resources/<公众号名>"
"""
import argparse
import hashlib
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor

import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from wechat_to_obsidian import download_image, guess_ext, html_lib

REMOTE = re.compile(r"!\[[^\]]*\]\((https?://[^)\s]+)\)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="公众号笔记目录（含 attachments/）")
    args = ap.parse_args()

    NOTE_DIR = pathlib.Path(args.dir)
    ATT = NOTE_DIR / "attachments"
    ATT.mkdir(parents=True, exist_ok=True)

    plan = {}
    for p in sorted(NOTE_DIR.glob("*.md")):
        txt = p.read_text(encoding="utf-8")
        urls = []
        for m in REMOTE.finditer(txt):
            u = html_lib.unescape(m.group(1))
            if "mmbiz.qpic.cn" in u or "mmbiz.qlogo.cn" in u:
                urls.append((m.group(1), u))
        if urls:
            plan[p] = urls

    total = sum(len(v) for v in plan.values())
    print("含远程图片的笔记: %d 篇, 共 %d 张\n" % (len(plan), total))
    if not total:
        print("无需处理。")
        return

    def job(u):
        safe = hashlib.md5(u.encode("utf-8")).hexdigest()[:10]
        ext = guess_ext(u)
        dest = ATT / f"{safe}{ext}"
        if not (dest.exists() and dest.stat().st_size > 0):
            if not download_image(u, dest):
                return u, None
        return u, dest

    all_urls = sorted({u for v in plan.values() for _, u in v})
    results = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for u, d in ex.map(job, all_urls):
            results[u] = d

    ok = fail = 0
    for p, pairs in plan.items():
        txt = p.read_text(encoding="utf-8")
        changed = False
        for raw, u in pairs:
            d = results.get(u)
            if d is None:
                fail += 1
                continue
            new = f"attachments/{d.name}"
            if f"]({raw})" in txt:
                txt = txt.replace(f"]({raw})", f"]({new})")
                changed = True
                ok += 1
        if changed:
            p.write_text(txt, encoding="utf-8")

    print("替换成功: %d 张 | 下载失败: %d 张" % (ok, fail))


if __name__ == "__main__":
    main()
