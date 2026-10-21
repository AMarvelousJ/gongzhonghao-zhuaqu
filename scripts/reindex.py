#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""非破坏性地重建 00-索引.md：只按现有笔记重排，不删任何东西。

增量更新后用它刷新索引，比 finalize.py 安全（finalize 会做去重删除，
每次更新都跑一遍有把「隔几天重发的同一活动通知」误删的风险）。
需要真去重时再单独跑 finalize.py --apply。
"""
import argparse
import pathlib
import re
import sys
from datetime import datetime

FM_DATE = re.compile(r'^date:\s*(\S+)\s*$', re.M)
FM_TITLE = re.compile(r'^title:\s*"?(.+?)"?\s*$', re.M)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="公众号笔记目录，如 <vault>/Resources/橘AI")
    ap.add_argument("--account", default=None, help="公众号名；省略时取目录名")
    args = ap.parse_args()

    d = pathlib.Path(args.dir)
    if not d.is_dir():
        sys.exit(f"目录不存在: {d}")
    account = args.account or d.name

    items = []
    for p in sorted(d.glob("*.md")):
        if p.name.startswith("00-"):
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        m = FM_DATE.search(t)
        date = m.group(1) if m else ""
        items.append((date, p.stem))

    items.sort(key=lambda x: x[0], reverse=True)
    lines = [
        "---",
        f'title: "{account} 文章索引"',
        "type: 索引",
        f"tags: [公众号/{account}]",
        f'updated: {datetime.now().strftime("%Y-%m-%d %H:%M")}',
        "---",
        "",
        f"# {account} 文章索引",
        "",
        f"共 {len(items)} 篇。",
        "",
    ]
    for date, stem in items:
        lines.append(f"- {date + ' ' if date else ''}[[{stem}]]")
    idx = d / "00-索引.md"
    idx.write_text("\n".join(lines) + "\n", encoding="utf-8")

    dates = [x[0] for x in items if x[0]]
    print(f"索引重建完成：{len(items)} 篇 → {idx}")
    if dates:
        print(f"日期范围：{min(dates)} ~ {max(dates)}")


if __name__ == "__main__":
    main()
