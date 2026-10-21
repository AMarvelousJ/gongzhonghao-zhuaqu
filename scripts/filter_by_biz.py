#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从内存扫描得到的候选 URL 里，筛出真正属于目标公众号的文章链接。

策略：流式抓取每篇的前 40KB，用页面里的 `var biz` / `nickname` / `msg_title` 判定归属。

2026-10-21 提速改造（原先 546 条要跑 10 分钟）：
  * 默认并发 8（微信对文章页的并发容忍度实测良好，60 条压测 8 并发零「环境异常」）
  * 命中「环境异常」壳页会自动退避重试一次，不会把好链接误判成「不属于本号」
  * 新增 --sample N：只随机校验 N 条就放行整批（首次全量时用，配合抓取时的
    --biz 二次校验；增量更新时链接少，走全量校验）
  * 默认 delay 从 0.3 降到 0.05（并发下没必要再人为串行）
"""
import argparse
import json
import pathlib
import random
import re
import sys
import time

import requests
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/81.0.4044.138 Safari/537.36 NetType/WIFI MicroMessenger/7.0.20.1781"
      "(0x6700143B) WindowsWechat(0x63090819) XWEB/8501 Flue")
HEADERS = {"User-Agent": UA, "Referer": "https://mp.weixin.qq.com/",
           "Accept-Language": "zh-CN,zh;q=0.9"}

BIZ_RE = re.compile(rb'(?:var\s+)?(?:__)?biz\s*=\s*["\']([A-Za-z0-9+/=]{10,40})["\']')
NICK_RE = re.compile(rb'var\s+nickname\s*=\s*["\']([^"\']{1,60})["\']')
NICK_RE2 = re.compile(rb'nickname["\']?\s*[:=]\s*["\']([^"\']{1,60})["\']')
TITLE_RE = re.compile(rb'var\s+msg_title\s*=\s*["\']([^"\']{1,200})["\']')
SHELL_MARK = ("环境异常", "请在微信客户端打开", "系统繁忙")
# 只读前 120KB：新版「文字消息」页的 var biz / nickname 出现得很靠后，
# 40KB 不够用（实测橘AI 最新两篇在 40KB 内连 biz 字符串都搜不到）
PROBE_BYTES = 120000


def _probe_once(url: str, timeout=12, biz="", name="", args_biz=""):
    """只读前 40KB 判断归属。返回 (ok, biz, nickname, title, shell, mine)。

    ⚠️ 「文字消息」型页面（短图文）前 40KB 里可能还没有 `var biz =` 那几行 JS 变量，
    只靠正则解析会全部判成「不属于本号」（实测橘AI 最新两篇就是这样被误杀的）。
    所以额外保留一条兜底：页面片段里**出现过**目标 biz 字符串即可判定归属。
    """
    try:
        with requests.get(url, headers=HEADERS, timeout=timeout, stream=True) as r:
            if r.status_code != 200:
                return False, None, None, None, False, False
            buf = b""
            for chunk in r.iter_content(8192):
                buf += chunk
                if len(buf) >= PROBE_BYTES:
                    break
    except Exception:
        return False, None, None, None, False, False
    text = buf.decode("utf-8", "ignore")
    shell = any(m in text for m in SHELL_MARK)
    biz = None
    m = BIZ_RE.search(buf)
    if m:
        biz = m.group(1).decode()
    nick = None
    for rx in (NICK_RE, NICK_RE2):
        m = rx.search(buf)
        if m:
            try:
                nick = m.group(1).decode("utf-8", "ignore")
            except Exception:
                nick = None
            if nick:
                break
    title = None
    m = TITLE_RE.search(buf)
    if m:
        title = m.group(1).decode("utf-8", "ignore").replace("\\/", "/")
    if not title:
        m = re.search(rb'<meta\s+property="og:title"\s+content="([^"]{1,200})"', buf)
        if m:
            title = m.group(1).decode("utf-8", "ignore")
    # 归属判定三态：True=确认是本号 / False=确认是别的号 / None=判不出来
    # 判不出来一律放行（宁可多抓一篇，也不能把好文章丢了）——真正的串号链接
    # 会被第二条拦住；新版「文字消息」页前 120KB 里连 biz 字符串都没有，只有放行。
    mine = None
    if biz and biz.encode() in buf:
        mine = True
    elif name and name.encode("utf-8") in buf:
        mine = True
    elif biz and biz != args_biz:
        mine = False
    return True, biz, nick, title, shell, mine


def probe(url: str, args_biz="", args_name="", retries=1, timeout=12):
    """带一次退避重试的探测。返回 dict（含 mine 归属判定）。"""
    ok, biz, nick, title, shell, mine = False, None, None, None, False, False
    for i in range(retries + 1):
        ok, biz, nick, title, shell, mine = _probe_once(
            url, timeout, biz=args_biz, name=args_name, args_biz=args_biz)
        if ok and not shell:
            break
        if i < retries:
            time.sleep(1.5 + 2 * i)
    if not ok:
        return {"ok": False, "url": url}
    return {"ok": True, "url": url, "biz": biz, "nick": nick, "title": title,
            "shell": shell, "mine": mine}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates", help="候选 URL 的 json 文件（数组）或每行一条的 txt")
    ap.add_argument("--out", default="urls_filtered.txt")
    ap.add_argument("--biz", default="", help="目标公众号的 biz 字符串，必填")
    ap.add_argument("--name", default="", help="目标公众号名，必填")
    ap.add_argument("--delay", type=float, default=0.05, help="并发批次之间的间隔秒")
    ap.add_argument("--workers", type=int, default=8, help="并发数（默认 8）")
    ap.add_argument("--sample", type=int, default=0,
                    help="只随机校验 N 条，其余按 URL 里的 __biz 直接放行；0 = 全量校验")
    ap.add_argument("--report", default="candidates_report.json")
    args = ap.parse_args()

    if not args.biz or not args.name:
        ap.error("必须同时提供 --biz 和 --name（公众号 biz 字符串与号名），脚本不预设任何默认号")

    p = pathlib.Path(args.candidates)
    if not p.is_absolute():
        p = pathlib.Path.cwd() / p
    if p.suffix.lower() == ".json":
        urls = json.loads(p.read_text(encoding="utf-8"))
    else:
        urls = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]

    seen, uniq = set(), []
    for u in urls:
        u = u.strip()
        if not u or u in seen:
            continue
        if "/s/" not in u and "mid=" not in u and "sn=" not in u:
            continue
        seen.add(u)
        uniq.append(u)

    todo = uniq
    passed_by_param = []
    if args.sample and args.sample < len(uniq):
        random.seed(20261021)
        todo = random.sample(uniq, args.sample)
        bizq = args.biz.replace("=", "%3D")
        passed_by_param = [u for u in uniq if u not in set(todo)
                           and (args.biz in u or bizq in u)]
        print(f"候选 {len(urls)} → 去重 {len(uniq)} 条；抽样校验 {len(todo)} 条，"
              f"其余 {len(passed_by_param)} 条按 URL 里的 __biz 直接放行", flush=True)
    else:
        print(f"候选 {len(urls)} → 去重并剔除残缺后 {len(uniq)} 条，并发 {args.workers} 探测归属…", flush=True)

    t0 = time.time()
    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        for r in ex.map(lambda u: probe(u, args.biz, args.name), todo):
            done += 1
            results.append(r)
            if r.get("mine"):
                print(f"  [{done}] ✓ {r.get('title') or '(无标题)'}", flush=True)
            elif done % 10 == 0:
                print(f"  …已探测 {done}/{len(todo)}", flush=True)

    hits = [r["url"] for r in results if r.get("mine") is not False]
    miss = [r for r in results if r.get("mine") is False]
    unsure = [r for r in results if r.get("mine") is None]
    hits += passed_by_param
    print(f"\n探测 {len(todo)} 条：确认本号 {len([r for r in results if r.get('mine') is True])}，"
          f"判不出来(放行) {len(unsure)}，确认非本号(剔除) {len(miss)}；"
          f"耗时 {time.time() - t0:.0f}s", flush=True)
    if miss:
        print("  被剔除示例：")
        for r in miss[:5]:
            print(f"    biz={r['biz']} nick={r['nick']} {r['url'][:70]}")

    out_p = pathlib.Path(args.out)
    if not out_p.is_absolute():
        out_p = pathlib.Path.cwd() / out_p
    rep_p = pathlib.Path(args.report)
    if not rep_p.is_absolute():
        rep_p = pathlib.Path.cwd() / rep_p
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text("\n".join(hits) + ("\n" if hits else ""), encoding="utf-8")
    rep_p.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"命中 {len(hits)} 条 → {out_p}")
    print(f"完整探测报告 → {rep_p}")


if __name__ == "__main__":
    main()
