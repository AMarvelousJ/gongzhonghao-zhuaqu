#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收尾工具：按 source 去重（同一篇文章被反复导入会留下 (2)(3) 副本，
同名但不同 URL 的两篇要都保留），并重建完整的 00-索引.md。
"""
import argparse
import pathlib
import re
from datetime import datetime

FM_TITLE = re.compile(r'^title:\s*"?(.+?)"?\s*$', re.M)
FM_SRC = re.compile(r'^source:\s*(\S+)\s*$', re.M)
FM_DATE = re.compile(r'^date:\s*(\S+)\s*$', re.M)
FM_IMPORTED = re.compile(r'^imported:\s*(.+?)\s*$', re.M)


def read_fm(p: pathlib.Path):
    t = p.read_text(encoding="utf-8", errors="ignore")
    if not t.startswith("---"):
        return {}, ""
    end = t.find("\n---", 3)
    fm = t[3:end]
    body = t[end + 4:]
    d = {}
    for key, rx in [("title", FM_TITLE), ("source", FM_SRC), ("date", FM_DATE),
                    ("imported", FM_IMPORTED)]:
        m = rx.search(fm)
        if m:
            d[key] = m.group(1).strip()
    return d, body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--account", default=None,
                    help="公众号名，用于生成索引标题；省略时取 --dir 的目录名")
    ap.add_argument("--apply", action="store_true", help="真的删除；不加则只预览")
    args = ap.parse_args()

    d = pathlib.Path(args.dir)
    account = args.account or d.name
    files = [p for p in sorted(d.glob("*.md")) if p.name != "00-索引.md"]

    # 1) 清理空壳
    shells = [p for p in files if re.fullmatch(r"未命名-\d+", p.stem)]
    for p in shells:
        print(f"  [删空壳] {p.name}")
        if args.apply:
            p.unlink()
    files = [p for p in files if p not in shells]

    # 2) 判重键用 mid + idx：同一篇文章可能是 /s/<id> 短链，也可能是
    #    s?__biz=..&mid=..&sn=.. 分享链，两种 URL 会同时存在。
    #    ⚠️ 必须带上 idx：一次群发（同一个 mid）里常常有 idx=1 / idx=2 两篇
    #    完全不同的文章，只用 mid 判重会误删其中一篇真实文章。
    def key_of(fm, p):
        src = fm.get("source", "")
        m = re.search(r"mid=(\d+)", src)
        if m:
            i = re.search(r"idx=(\d+)", src)
            return "mid:%s:%s" % (m.group(1), i.group(1) if i else "1")
        m = re.search(r"/s/([A-Za-z0-9_-]{16,30})", src)
        if m:
            return "s:" + m.group(1)
        return "name:" + p.stem

    by_src = {}
    for p in files:
        fm, body = read_fm(p)
        key = key_of(fm, p)
        # 优先保留「最新导入」的版本：早期抓坏的模板文本可能比正经正文还长，
        # 单比长度会挑错。
        score = (fm.get("imported", ""), len(body.strip()))
        if key not in by_src or score > by_src[key][1]:
            by_src[key] = (p, score, fm, body)

    keep = {v[0] for v in by_src.values()}
    dups = [p for p in files if p not in keep]
    for p in dups:
        print(f"  [删重复] {p.name}")
        if args.apply:
            p.unlink()

    # 2b) 把侥幸留下来的 "xxx (2).md" 改回干净名字（目标名没被占才改）
    used = {p.name for p in d.glob("*.md") if p.name != "00-索引.md"}
    # ⚠️ 必须把改名后的新路径写回 by_src：下面 2c 还要按 by_src 里的路径删除
    # 重复项，不写回的话它会去 unlink 一个已经 rename 掉的旧路径，
    # 直接 FileNotFoundError 崩掉（实测 755 篇时触发）。
    for key, (p, score, fm, body) in list(by_src.items()):
        m = re.search(r"^(.*) \((\d+)\)$", p.stem)
        if not m:
            continue
        want = d / (m.group(1) + ".md")
        if want.name in used:
            continue  # 同名不同文，保留后缀区分
        print(f"  [改回名] {p.name} → {want.name}")
        if args.apply:
            p.rename(want)
            used.discard(p.name)
            used.add(want.name)
            by_src[key] = (want, score, fm, body)

    # 2c) 二次去重：短链 /s/<id> 与分享链拿不到同一个 mid，
    #     用「标题 + 正文开头指纹」再收一遍。同名不同文（正文不同）不会误删。
    fp_map, merged = {}, {}
    extra_dups = []
    for key, (p, score, fm, body) in by_src.items():
        fp = (fm.get("title", p.stem), body.strip()[:150])
        if fp not in fp_map or score > fp_map[fp][1]:
            if fp in fp_map:
                extra_dups.append(fp_map[fp][0])
            fp_map[fp] = (p, score, fm, body)
        else:
            extra_dups.append(p)
    for p in extra_dups:
        print(f"  [删重复/同文异链] {p.name}")
        if args.apply and p.exists():
            p.unlink()
    by_src = {(k[0] if isinstance(k, tuple) else k): v for k, v in
              {("s:" + str(i)): v for i, v in enumerate(fp_map.values())}.items()}

    # 3) 重建索引
    items = []
    for p, _, fm, _ in by_src.values():
        items.append((fm.get("date", ""), p.stem, fm.get("title", "")))
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
    for date, stem, _ in items:
        prefix = f"{date} " if date else ""
        lines.append(f"- {prefix}[[{stem}]]")
    idx = d / "00-索引.md"
    idx.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n去重后 {len(items)} 篇，删除空壳 {len(shells)} + 重复 {len(dups)}")
    print("索引 →", idx)
    if not args.apply:
        print("（预览模式，未实际删除；加 --apply 生效）")


if __name__ == "__main__":
    main()
