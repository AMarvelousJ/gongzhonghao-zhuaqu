#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从内存扫描得到的候选 URL 里，筛出真正属于目标公众号的文章链接。

策略：流式抓取每篇的前 40KB，用页面里的 `var biz` / `nickname` / `msg_title` 判定归属。
比整篇抓取快得多，且不落盘垃圾。
"""
import argparse
import json
import pathlib
import re
import sys
import time

import requests

# 默认值留空：真实 biz / 号名必须由命令行传入，避免不同机器之间串号
TARGET_BIZ = ""
TARGET_NAME = ""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
      "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
      "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a1b)")
HEADERS = {"User-Agent": UA, "Referer": "https://mp.weixin.qq.com/",
           "Accept-Language": "zh-CN,zh;q=0.9"}

BIZ_RE = re.compile(rb'(?:var\s+)?(?:__)?biz\s*=\s*["\']([A-Za-z0-9+/=]{10,40})["\']')
NICK_RE = re.compile(rb'var\s+nickname\s*=\s*["\']([^"\']{1,60})["\']')
NICK_RE2 = re.compile(rb'nickname["\']?\s*[:=]\s*["\']([^"\']{1,60})["\']')
TITLE_RE = re.compile(rb'var\s+msg_title\s*=\s*["\']([^"\']{1,200})["\']')
DATE_RE = re.compile(rb'var\s+ct\s*=\s*["\']?(\d{9,11})')


def probe(url: str, timeout=12, args_biz=TARGET_BIZ, args_name=TARGET_NAME):
    """只读前 40KB 判断归属。返回 (ok, biz, nickname, title)。"""
    try:
        with requests.get(url, headers=HEADERS, timeout=timeout, stream=True) as r:
            if r.status_code != 200:
                return False, None, None, None
            buf = b""
            for chunk in r.iter_content(8192):
                buf += chunk
                if len(buf) >= 40000:
                    break
    except Exception:
        return False, None, None, None
    text = buf.decode("utf-8", "ignore")
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
    # 最可靠的判定：页面里那几行是
    #   var biz = "MzA5NjM4MDAxMg==" || ""
    #   var nickname = htmlDecode("示例公众号")
    #   var user_name = "gh_xxxxxxxxxxxx"
    # 前两个由 --biz / --name 指定，运行时逐个页面比对
    biz_hit = args_biz.encode() in buf
    name_hit = args_name.encode("utf-8") in buf
    biz = args_biz if biz_hit else None
    nick = args_name if name_hit else nick
    return True, biz, nick, title


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates", help="候选 URL 的 json 文件（数组）")
    ap.add_argument("--out", default="urls_filtered.txt")
    ap.add_argument("--biz", default=TARGET_BIZ, help="目标公众号的 biz 字符串，必填")
    ap.add_argument("--name", default=TARGET_NAME, help="目标公众号名，必填")
    ap.add_argument("--delay", type=float, default=0.3)
    ap.add_argument("--report", default="candidates_report.json")
    args = ap.parse_args()

    if not args.biz or not args.name:
        ap.error("必须同时提供 --biz 和 --name（公众号 biz 字符串与号名），脚本不预设任何默认号")

    # 相对路径一律按「当前工作目录」解析：早期版本拼的是脚本所在目录，
    # 会导致输入找不到、输出还写进 skill 自己的 scripts/ 里。
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
        # 只有 __biz 没有 mid/sn 的才是残缺链接；带 sn 的分享链等价于 /s/<id>
        if "/s/" not in u and "mid=" not in u and "sn=" not in u:
            continue
        seen.add(u)
        uniq.append(u)

    print(f"候选 {len(urls)} → 去重并剔除残缺后 {len(uniq)} 条，开始探测归属…", flush=True)

    hits, allinfo = [], []
    for i, u in enumerate(uniq, 1):
        ok, biz, nick, title = probe(u, args_biz=args.biz, args_name=args.name)
        allinfo.append({"url": u, "ok": ok, "biz": biz, "nick": nick, "title": title})
        mine = (biz == args.biz) or (nick and args.name in nick)
        if mine:
            hits.append(u)
            print(f"  [{len(hits)}] ✓ {title or '(无标题)'}", flush=True)
        if i % 25 == 0:
            print(f"  …已探测 {i}/{len(uniq)}，命中 {len(hits)}", flush=True)
        time.sleep(args.delay)

    out_p = pathlib.Path(args.out)
    if not out_p.is_absolute():
        out_p = pathlib.Path.cwd() / out_p
    rep_p = pathlib.Path(args.report)
    if not rep_p.is_absolute():
        rep_p = pathlib.Path.cwd() / rep_p
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text("\n".join(hits) + ("\n" if hits else ""), encoding="utf-8")
    rep_p.write_text(json.dumps(allinfo, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n命中 {len(hits)} 条 → {out_p}")
    print(f"完整探测报告 → {rep_p}")


if __name__ == "__main__":
    main()
