#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令跑完「公众号 → Obsidian」的全流程，并且天然支持增量。

首次：
    python wechat_sync.py --account 橘AI --biz MzkwMzY5NzU2Nw== --vault D:\\vibecoding\\Obsidian\\WhisperSands

以后（biz / vault 都记在注册表里，直接一句）：
    python wechat_sync.py --account 橘AI          # 只抓上次之后的新文章
    python wechat_sync.py --all                   # 注册表里所有号轮流更新

流程：
    ① 内存扫描拿候选链接（并发扫进程，约 40~60s）
    ② 按 biz 清洗去重
    ③ 用现有笔记 frontmatter 里的 mid+idx 算出「已知集合」，diff 出新链接
    ④ 只抓新链接（并发 6，带归属二次校验）
    ⑤ 全量重建索引（非破坏性），更新注册表

注册表：  ~/.workbuddy/state/gongzhonghao-zhuaqu/accounts.json
中间产物：~/.workbuddy/state/gongzhonghao-zhuaqu/work/<号名>/
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
PY = sys.executable
STATE_DIR = pathlib.Path.home() / ".workbuddy" / "state" / "gongzhonghao-zhuaqu"
REGISTRY = STATE_DIR / "accounts.json"

MID_RE = re.compile(r"mid=(\d+)")
IDX_RE = re.compile(r"idx=(\d+)")
SID_RE = re.compile(r"/s/([A-Za-z0-9_-]{16,30})")
FM_SRC = re.compile(r'^source:\s*(\S+)\s*$', re.M)


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- 注册表


def load_registry():
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_registry(reg):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


def run(cmd, cwd=None, check=True):
    log("  $ " + " ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.stdout:
        print(r.stdout.rstrip(), flush=True)
    if r.returncode != 0:
        tail = (r.stderr or "").strip()[-800:]
        log("  [命令返回 %d] %s" % (r.returncode, tail))
        if check:
            sys.exit(f"步骤失败：{' '.join(str(c) for c in cmd)}")
    return r


# ---------------------------------------------------------------- 已知集合


def link_key(url: str) -> str:
    m = MID_RE.search(url)
    if m:
        i = IDX_RE.search(url)
        return "mid:%s:%s" % (m.group(1), i.group(1) if i else "1")
    m = SID_RE.search(url)
    if m:
        return "s:" + m.group(1)
    return "u:" + url


def known_keys(notes_dir: pathlib.Path):
    """从已有笔记的 frontmatter 里还原「已经抓过哪些文章」。"""
    keys = set()
    if not notes_dir.is_dir():
        return keys
    for p in notes_dir.glob("*.md"):
        if p.name.startswith("00-"):
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = FM_SRC.search(t)
        if m:
            keys.add(link_key(m.group(1)))
    return keys


def resolve_biz(account, biz, reg):
    if biz:
        return biz
    info = reg.get(account) or {}
    if info.get("biz"):
        return info["biz"]
    return ""


def sync_account(account, biz, vault, mode="auto", workers=6, sample=30,
                 dry_run=False, no_scan=False, links_file=None):
    t0 = time.time()
    reg = load_registry()
    info = reg.get(account) or {}
    if not biz:
        biz = info.get("biz", "")
    if not vault:
        vault = info.get("vault", "")
    if not vault:
        sys.exit("缺 --vault（注册表里也没有这个号的库路径）")
    vault = pathlib.Path(vault)
    if not vault.is_dir():
        sys.exit(f"Obsidian 库不存在: {vault}")
    notes_dir = vault / "Resources" / account
    work = STATE_DIR / "work" / re.sub(r'[\\/:*?"<>|]', "_", account)
    work.mkdir(parents=True, exist_ok=True)

    if not biz:
        sys.exit(f"不知道「{account}」的 biz。第一次抓请带 --biz；"
                 f"也可以用 mem_ctx_scan.py --name \"{account}\" 定位。")

    log(f"== {account} ==  biz={biz}")
    log(f"   笔记目录: {notes_dir}")

    # ① 候选链接
    if links_file:
        links_src = pathlib.Path(links_file)
        log(f"① 使用已有链接文件: {links_src}")
        raw = links_src
    else:
        raw = work / "mem_links.json"
        if no_scan and raw.exists():
            log(f"① 复用上次扫描结果: {raw}")
        else:
            log("① 扫描微信进程内存…")
            run([PY, "-u", HERE / "mem_scan_links.py", raw, "--workers", "4", "--biz", biz], cwd=work)

    # ② 清洗
    cleaned = work / "urls_clean.txt"
    log("② 清洗去重…")
    run([PY, HERE / "clean_links.py", raw, cleaned, "--biz", biz], cwd=work)
    all_links = [l.strip() for l in cleaned.read_text(encoding="utf-8").splitlines() if l.strip()]

    # ③ diff
    old_keys = known_keys(notes_dir)
    existing = notes_dir.is_dir() and len(list(notes_dir.glob("*.md"))) > 1
    if mode == "full" or not existing:
        new_links = all_links
        log(f"③ 首次/全量模式：{len(new_links)} 条全部抓取")
    else:
        new_links = [u for u in all_links if link_key(u) not in old_keys]
        log(f"③ 库里已有 {len(old_keys)} 篇，候选 {len(all_links)} 条 → 新增 {len(new_links)} 条")
        if not new_links:
            reg[account] = dict(info, biz=biz, vault=str(vault), account=account,
                                last_sync=datetime.now().strftime("%Y-%m-%d %H:%M"),
                                total=len(old_keys), new=0)
            save_registry(reg)
            log("   没有新文章，收工。")
            return {"account": account, "new": 0, "total": len(old_keys), "seconds": time.time() - t0}

    if dry_run:
        log(f"④ --dry-run：下面 {len(new_links)} 条会被抓取（前 10 条）")
        for u in new_links[:10]:
            log("   " + u[:110])
        return {"account": account, "new": len(new_links), "total": len(old_keys), "dry_run": True}

    # ④ 归属校验：新链接少就全量校验，多就抽样（抓取阶段还有一道 --biz 兜底）
    final = work / "urls_final.txt"
    (work / "urls_new.txt").write_text("\n".join(new_links) + "\n", encoding="utf-8")
    if len(new_links) <= 60:
        log(f"④ 校验归属（{len(new_links)} 条全量）…")
        run([PY, "-u", HERE / "filter_by_biz.py", work / "urls_new.txt", "--biz", biz,
             "--name", account, "--out", final, "--report", work / "report.json",
             "--workers", "8"], cwd=work)
    else:
        log(f"④ 抽样校验 {sample} 条（抓取时再做逐篇 biz 校验）…")
        run([PY, "-u", HERE / "filter_by_biz.py", work / "urls_new.txt", "--biz", biz,
             "--name", account, "--out", final, "--report", work / "report.json",
             "--workers", "8", "--sample", sample], cwd=work)

    # ⑤ 抓取（不重建索引，稍后全量重建）
    log(f"⑤ 抓取新文章（并发 {workers}）…")
    run([PY, "-u", HERE / "wechat_to_obsidian.py", "--urls", final, "--vault", vault,
         "--account", account, "--images", "local", "--workers", workers,
         "--biz", biz, "--no-index"], cwd=work)

    # ⑥ 重建索引
    log("⑥ 重建索引…")
    run([PY, HERE / "reindex.py", "--dir", notes_dir, "--account", account])

    total = len([p for p in notes_dir.glob("*.md") if not p.name.startswith("00-")])
    reg[account] = dict(info, biz=biz, vault=str(vault), account=account,
                        last_sync=datetime.now().strftime("%Y-%m-%d %H:%M"),
                        total=total, new=len(new_links))
    save_registry(reg)
    log(f"== 完成：新增 {len(new_links)} 篇，库内共 {total} 篇，"
        f"耗时 {time.time() - t0:.0f}s ==")
    return {"account": account, "new": len(new_links), "total": total,
            "seconds": round(time.time() - t0)}


def main():
    ap = argparse.ArgumentParser(description="公众号文章 → Obsidian（支持增量）")
    ap.add_argument("--account", help="公众号名称")
    ap.add_argument("--biz", default="", help="公众号 biz；首次必填，之后记在注册表里")
    ap.add_argument("--vault", default="", help="Obsidian 库根目录")
    ap.add_argument("--mode", choices=["auto", "full"], default="auto",
                    help="auto=只抓新文章（默认）；full=无视已有笔记重抓全部")
    ap.add_argument("--workers", type=int, default=6, help="抓取并发（默认 6）")
    ap.add_argument("--sample", type=int, default=30, help="首次全量时的抽样校验条数")
    ap.add_argument("--dry-run", action="store_true", help="只算新文章，不抓")
    ap.add_argument("--no-scan", action="store_true", help="复用上次内存扫描结果（调试用）")
    ap.add_argument("--links", help="直接给一个 URL 列表文件，跳过内存扫描")
    ap.add_argument("--all", action="store_true", help="更新注册表中所有公众号")
    ap.add_argument("--list", action="store_true", help="列出注册表里已收录的公众号")
    args = ap.parse_args()

    reg = load_registry()
    if args.list:
        if not reg:
            print("注册表还是空的:", REGISTRY)
        for k, v in reg.items():
            print(f"  {k}: biz={v.get('biz')} 共{v.get('total')}篇 "
                  f"上次同步 {v.get('last_sync')} 新增{v.get('new')}")
        return

    if args.all:
        if not reg:
            sys.exit("注册表为空，先手动同步一个号")
        out = []
        for name, info in reg.items():
            out.append(sync_account(name, info.get("biz", ""), info.get("vault", ""),
                                    mode=args.mode, workers=args.workers, sample=args.sample,
                                    dry_run=args.dry_run))
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return

    if not args.account:
        ap.error("需要 --account 或 --all 或 --list")
    sync_account(args.account, args.biz, args.vault, mode=args.mode, workers=args.workers,
                 sample=args.sample, dry_run=args.dry_run, no_scan=args.no_scan,
                 links_file=args.links)


if __name__ == "__main__":
    main()
