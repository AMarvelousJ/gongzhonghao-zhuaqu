#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把正文里残留的**非微信图床**外链图片下载并本地化。

背景：wechat_to_obsidian.py 只处理 mmbiz.qpic.cn / mmbiz.qlogo.cn 这类微信图。
少数老文章里作者直接贴了外部图床（实测「快刀青衣」2014 年几篇用了
img.huxiu.com / u.img.huxiu.com），这些图会以裸 http 外链留在正文里，
check_images.py 会把它算成「引用了但磁盘上没有」，fix_remote.py 又救不了
（它只认 http 开头的引用，认不出已经被改写成本地路径的）。

用法:
    python fix_ext_img.py --dir "<vault>/Resources/<公众号名>"
外站有防盗链时手动改 HEADERS 里的 Referer。
"""
import argparse
import hashlib
import pathlib
import re
import sys

import requests

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36 NetType/WIFI "
                   "MicroMessenger/7.0.20.1781(0x6700143B) WindowsWechat(0x63090a13)"),
    "Referer": "https://mp.weixin.qq.com/",
}
EXT_BY_CT = [("png", ".png"), ("gif", ".gif"), ("webp", ".webp")]


def pick_ext(url, content_type):
    for key, ext in EXT_BY_CT:
        if key in content_type:
            return ext
    if url.lower().endswith((".png", ".gif", ".webp", ".jpeg")):
        return "." + url.lower().rsplit(".", 1)[1]
    return ".jpg"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="笔记目录（其下应有 attachments/）")
    ap.add_argument("--referer", default=None, help="覆盖 Referer，外站防盗链时用")
    args = ap.parse_args()

    d = pathlib.Path(args.dir)
    att = d / "attachments"
    if not att.is_dir():
        sys.exit(f"[错误] 找不到附件目录：{att}")
    if args.referer:
        HEADERS["Referer"] = args.referer

    ok = fail = 0
    for md in sorted(d.glob("*.md")):
        if md.name.startswith("00-"):
            continue
        text = md.read_text(encoding="utf-8")
        urls = re.findall(r"!\[\]\((https?://[^)]+)\)", text)
        if not urls:
            continue
        new = text
        for u in urls:
            try:
                r = requests.get(u, headers=HEADERS, timeout=25)
                ct = r.headers.get("Content-Type", "")
                good = (r.status_code == 200 and len(r.content) > 1000
                        and ("image" in ct or u.lower().endswith(
                            (".jpg", ".jpeg", ".png", ".gif", ".webp"))))
                if good:
                    ext = pick_ext(u, ct)
                    name = hashlib.md5(u.encode()).hexdigest()[:10] + ext
                    (att / name).write_bytes(r.content)
                    new = new.replace(f"![]({u})", f"![](attachments/{name})")
                    ok += 1
                    print(f"  OK   {md.name[:40]} -> {name}")
                else:
                    fail += 1
                    print(f"  FAIL {md.name[:40]}  status={r.status_code} "
                          f"bytes={len(r.content)} ct={ct}")
            except Exception as e:
                fail += 1
                print(f"  ERR  {md.name[:40]}  {str(e)[:70]}")
        if new != text:
            md.write_text(new, encoding="utf-8")

    print(f"\n下载成功 {ok}，失败 {fail}"
          + ("（失败的会保留外链，Obsidian 联网时仍可显示）" if fail else ""))


if __name__ == "__main__":
    main()
