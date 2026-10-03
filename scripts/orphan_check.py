#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理前确认：attachments 里的孤儿图片，在整个 Obsidian 库里真的没人引用吗？
并按 md5 区分「被引用图的重复副本」与「内容唯一的旧版残留」。

用法:
    python orphan_check.py --vault "<库根>" --dir "<vault>/Resources/<公众号名>"
                           --out orphan_list.json
"""
import argparse
import hashlib
import json
import os
import pathlib
import re


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True, help="Obsidian 库根目录")
    ap.add_argument("--dir", required=True, help="公众号笔记目录")
    ap.add_argument("--out", default="orphan_list.json")
    args = ap.parse_args()

    VAULT = pathlib.Path(args.vault)
    NOTE_DIR = pathlib.Path(args.dir)
    ATT = NOTE_DIR / "attachments"

    files = [f for f in os.listdir(ATT) if (ATT / f).is_file()]
    refd = set()
    for p in NOTE_DIR.glob("*.md"):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for base in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", txt):
            refd.add(os.path.basename(base.split("?")[0]))
    orphan = [f for f in files if f not in refd]
    print("孤儿候选:", len(orphan), "/ 附件总数", len(files))

    names = {f: (f, os.path.splitext(f)[0]) for f in orphan}
    hit = {f: [] for f in orphan}
    md_files = list(VAULT.rglob("*.md"))
    print("全库 md 文件数:", len(md_files))
    for p in md_files:
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "attachments/" not in t and "[[" not in t:
            continue
        for f, (full, stem) in names.items():
            if full in t or f"[[{stem}" in t or f"[[{full}" in t:
                hit[f].append(str(p.relative_to(VAULT)))

    real_orphan = [f for f in orphan if not hit[f]]
    print("\n全库仍有引用（不能动）:", len(orphan) - len(real_orphan))
    for f in orphan:
        if hit[f]:
            print("   ", f, "->", hit[f][:3])
    print("确认无引用的真孤儿:", len(real_orphan))

    ref_hash = {}
    for f in files:
        if f in orphan:
            continue
        ref_hash.setdefault(md5(ATT / f), f)

    dup, uni = [], []
    for f in real_orphan:
        (dup if md5(ATT / f) in ref_hash else uni).append(f)
    sz = lambda l: sum((ATT / f).stat().st_size for f in l) / 1048576
    print("\n真孤儿中 与被引用图内容重复: %d 个 (%.1f MB)" % (len(dup), sz(dup)))
    print("真孤儿中 内容唯一(疑似旧版残留): %d 个 (%.1f MB)" % (len(uni), sz(uni)))

    pathlib.Path(args.out).write_text(
        json.dumps({"dup": dup, "unique": uni}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print("\n名单已写入:", args.out)


if __name__ == "__main__":
    main()
