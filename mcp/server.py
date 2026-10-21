#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公众号抓取 MCP 服务。

把 gongzhonghao-zhuaqu 这套脚本包装成 MCP 工具，Agent 里一句话就能跑：
    「更新一下橘AI的公众号文章」
    「看看橘AI有没有新文章」

前提：
  * Windows + 电脑上登录着 PC 微信（要扫进程内存拿文章链接）
  * 首次抓某个号时要把这个号的历史文章页打开、滚到底（之后增量更新不用）

工具：
  wechat_list_accounts   列出已收录的号
  wechat_preview_new     只算「有几篇新文章」，不抓（快）
  wechat_update_account  抓新文章并更新索引（默认增量）
  wechat_update_all      把注册表里所有号都更新一遍
  wechat_find_biz        不知道 biz 时，从微信内存里定位某个号的 biz

日志：~/.workbuddy/state/gongzhonghao-zhuaqu/mcp.log
"""
import json
import pathlib
import subprocess
import sys
import os
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
STATE = pathlib.Path.home() / ".workbuddy" / "state" / "gongzhonghao-zhuaqu"
LOG = STATE / "mcp.log"
PY = sys.executable

try:  # mcp 2.x：FastMCP 已改名 MCPServer
    from mcp.server.mcpserver import MCPServer
    FastMCP = MCPServer
except ImportError:  # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        sys.exit("需要先装 mcp：pip install mcp -i https://pypi.org/simple")

mcp = FastMCP("wechat-obsidian")


def _log(msg):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _run(args, timeout=1800, tail=4000):
    """跑一条子命令，返回 (returncode, 输出尾部)。"""
    cmd = [PY, "-u"] + [str(a) for a in args]
    _log("RUN " + " ".join(str(a) for a in args))
    try:
        r = subprocess.run(cmd, cwd=str(SCRIPTS), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    except subprocess.TimeoutExpired:
        return -1, f"超时（>{timeout}s）"
    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr[-1500:]) if r.stderr else "")
    _log(f"EXIT {r.returncode} len={len(out)}")
    if len(out) > tail:
        out = "…（前面省略）\n" + out[-tail:]
    return r.returncode, out.strip()


def _registry():
    reg = STATE / "accounts.json"
    if reg.exists():
        try:
            return json.loads(reg.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


@mcp.tool()
def wechat_list_accounts() -> str:
    """列出已经收录的公众号（biz、已有篇数、上次同步时间）。"""
    reg = _registry()
    if not reg:
        return "注册表还是空的。第一次抓请用 wechat_update_account 并带上 biz 与 vault 路径。"
    lines = []
    for name, info in reg.items():
        lines.append(f"- {name}: biz={info.get('biz')} 共{info.get('total')}篇 "
                     f"上次同步 {info.get('last_sync')}（那次新增{info.get('new')}）")
    return "\n".join(lines)


@mcp.tool()
def wechat_preview_new(account: str, biz: str = "", vault: str = "") -> str:
    """只看看某个公众号有没有新文章，不抓取。会扫描微信内存（约 45 秒）。

    account: 公众号名，如「橘AI」
    biz:     首次必须给；之后注册表里有，可省略
    vault:   Obsidian 库根目录，首次必须给
    """
    args = [SCRIPTS / "wechat_sync.py", "--account", account, "--dry-run"]
    if biz:
        args += ["--biz", biz]
    if vault:
        args += ["--vault", vault]
    code, out = _run(args, timeout=900)
    if code != 0:
        return f"失败（退出码 {code}）：\n{out}"
    keep = [l for l in out.splitlines()
            if l.startswith(("①", "②", "③", "④", "==", "   笔记目录")) or "新增" in l]
    return "\n".join(keep[-15:]) or out[-1500:]


@mcp.tool()
def wechat_update_account(account: str, biz: str = "", vault: str = "",
                          mode: str = "auto", workers: int = 6) -> str:
    """抓取某个公众号的文章到 Obsidian，默认只抓新文章（增量）。

    account: 公众号名，如「橘AI」
    biz:     首次必须给；之后注册表里有，可省略
    vault:   Obsidian 库根目录，如 D:\\vibecoding\\Obsidian\\WhisperSands；首次必须给
    mode:    auto=只抓新文章（默认）；full=无视已有笔记重抓全部
    workers: 并发数，默认 6
    注意：首次跑之前要让这个号的历史文章页在微信里打开并滚到底。
    """
    args = [SCRIPTS / "wechat_sync.py", "--account", account,
            "--mode", mode, "--workers", str(workers)]
    if biz:
        args += ["--biz", biz]
    if vault:
        args += ["--vault", vault]
    code, out = _run(args, timeout=3600)
    head = "完成 ✅" if code == 0 else f"失败（退出码 {code}）"
    tail_lines = [l for l in out.splitlines()
                  if l.startswith(("③", "⑤", "⑥", "==", "索引重建")) or "日期范围" in l]
    return head + "\n" + ("\n".join(tail_lines[-12:]) or out[-1500:])


@mcp.tool()
def wechat_update_all(mode: str = "auto", workers: int = 6) -> str:
    """把注册表里所有公众号都增量更新一遍（每个号都要扫一次微信内存，号多会比较久）。"""
    args = [SCRIPTS / "wechat_sync.py", "--all", "--mode", mode, "--workers", str(workers)]
    code, out = _run(args, timeout=7200)
    head = "全部完成 ✅" if code == 0 else f"失败（退出码 {code}）"
    return head + "\n" + out[-2500:]


@mcp.tool()
def wechat_find_biz(name: str) -> str:
    """不知道某个公众号的 biz 时用它：扫描微信进程内存，找号名附近出现的 __biz。

    用之前请先在微信里打开这个号的任意一篇文章或主页。
    name: 公众号名，如「橘AI」
    """
    out_file = STATE / "work" / "ctx_hits.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    code, out = _run([SCRIPTS / "mem_ctx_scan.py", "--name", name, "--out", out_file],
                     timeout=600, tail=2000)
    if code != 0:
        return f"失败（退出码 {code}）：\n{out}"
    try:
        hits = json.loads(out_file.read_text(encoding="utf-8"))
        return json.dumps(hits, ensure_ascii=False)[:2000]
    except Exception:
        return out[-1500:]


if __name__ == "__main__":
    _log("MCP server starting, python=" + PY)
    mcp.run(transport="stdio")
