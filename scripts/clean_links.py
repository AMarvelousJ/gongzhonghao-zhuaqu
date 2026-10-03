#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清洗从微信进程内存里挖出来的链接。

内存字符串常常是 "URL1\\x00\\x00URL2\\x00..." 这种粘连形态，
这里按合法 URL 字符集切开，只保留属于目标公众号的永久链接，
并按 mid+idx 去重、按 sn 完整度择优。

用法:
    python clean_links.py <mem_links.json> <输出.txt> --biz <biz字符串>
"""
import argparse
import json
import pathlib
import re
from urllib.parse import urlencode, unquote

URL_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s\?[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")


def norm(u: str) -> str:
    """统一成 https://mp.weixin.qq.com/s?__biz=..&mid=..&idx=..&sn=.. 形式"""
    q = u.split("?", 1)[1] if "?" in u else ""
    parts = {}
    for seg in q.split("&"):
        if "=" in seg:
            a, b = seg.split("=", 1)
            parts[a] = b
    keep = ["__biz", "mid", "idx", "sn"]
    out = {k: parts[k] for k in keep if k in parts}
    if "__biz" not in out or "mid" not in out:
        return ""
    return "https://mp.weixin.qq.com/s?" + urlencode(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="mem_scan_links.py 输出的 json 文件")
    ap.add_argument("dst", help="清洗后的 URL 列表输出文件")
    ap.add_argument("--biz", required=True, help='目标公众号 biz，如 "MzA5NjM4MDAxMg=="')
    args = ap.parse_args()

    raw = json.loads(pathlib.Path(args.src).read_text(encoding="utf-8"))
    target = args.biz.rstrip("=")

    # 同一篇（mid+idx）在内存里可能出现多次、完整度不同，统一收集后择优
    bucket = {}
    for blob in raw:
        for u in URL_RE.findall(blob):
            m = re.search(r"__biz=([A-Za-z0-9+/=%\-]+)", u)
            if not m:
                continue
            biz = unquote(unquote(m.group(1)))  # 内存里存在双重编码
            if biz.rstrip("=") != target:
                continue
            n = norm(u)
            if not n:
                continue
            mm = re.search(r"mid=(\d+)", n)
            ii = re.search(r"idx=(\d+)", n)
            ss = re.search(r"sn=([A-Za-z0-9]+)", n)
            key = (mm.group(1) if mm else n, ii.group(1) if ii else "1")
            score = len(ss.group(1)) if ss else 0
            if key not in bucket or score > bucket[key][0]:
                bucket[key] = (score, n)

    out = [v[1] for v in bucket.values()]
    out.sort(key=lambda u: int(re.search(r"mid=(\d+)", u).group(1))
             if re.search(r"mid=(\d+)", u) else 0, reverse=True)

    print(f"输入 {len(raw)} 段内存字符串 → 目标公众号去重后 {len(out)} 条")
    bad = [u for u in out if not re.search(r"sn=[A-Za-z0-9]{30,}", u)]
    print(f"其中 sn 可能被截断/缺失的：{len(bad)} 条（抓取时会校验，失败即丢弃）")
    pathlib.Path(args.dst).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("已存:", args.dst)
    for u in out[:5]:
        print("  ", u)


if __name__ == "__main__":
    main()
