#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在微信进程内存里搜索公众号名，输出命中位置附近的 __biz，用来定位 biz。

比「统计 mem_links.json 里各 biz 的文章数」更准：用户可能同时开着好几个号，
按数量排第一的不一定是目标号。这里直接搜名字，看名字旁边跟着哪个 biz。

用法:
    python mem_ctx_scan.py --name "哥飞"
    python mem_ctx_scan.py --name "刘小排" --out ctx_hits.json

同时试 UTF-8、UTF-16LE、拼音三种编码。结果里 biz_nearby 出现次数最多的那个基本就是目标。
仅 Windows。
"""
import argparse
import ctypes
import json
import pathlib
import re
import sys
from ctypes import wintypes

if sys.platform != "win32":
    sys.exit("[不支持] mem_ctx_scan.py 依赖 Windows API 读取进程内存，仅 Windows 可用。")

k32 = ctypes.windll.kernel32
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x2
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
READABLE = {0x02, 0x04, 0x20, 0x40}
CHUNK = 8 * 1024 * 1024
OVERLAP = 8192
WIN = 3000          # 命中位置前后各取 3KB 作为上下文
KEEP_PER_PROC = 12  # 每个进程最多留多少段上下文


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wintypes.DWORD), ("RegionSize", ctypes.c_size_t),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD)]


def pids_by_name(name):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    out, e = [], PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile == name:
            out.append(e.th32ProcessID)
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


BIZ_RE = re.compile(rb"__biz=([A-Za-z0-9+/=%\-]{10,40})")
URL_RE = re.compile(r"https?://mp\.weixin\.qq\.com/s\?[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")


def make_needles(word):
    n = {"utf8": word.encode("utf-8"), "utf16": word.encode("utf-16-le")}
    try:
        from urllib.parse import quote
        n["urlenc"] = quote(word).encode("ascii")
    except Exception:
        pass
    return n


def scan(pid, needles, hits, biz_counter):
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return 0, 0
    addr, total, n_hit = 0, 0, 0
    mbi = MBI()
    while addr < 0x7FFFFFFFFFFF:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        base, size = mbi.BaseAddress or 0, mbi.RegionSize or 0
        nxt = base + size
        if (mbi.State == MEM_COMMIT and mbi.Protect in READABLE
                and not (mbi.Protect & PAGE_GUARD) and size):
            off = 0
            while off < size:
                want = min(CHUNK + OVERLAP, size - off)
                buf = ctypes.create_string_buffer(want)
                got = ctypes.c_size_t()
                if k32.ReadProcessMemory(h, ctypes.c_void_p(base + off), buf, want, ctypes.byref(got)) and got.value:
                    total += got.value
                    data = buf.raw[:got.value]
                    for enc, needle in needles.items():
                        start = 0
                        while True:
                            i = data.find(needle, start)
                            if i < 0:
                                break
                            start = i + 1
                            n_hit += 1
                            lo, hi = max(0, i - WIN), min(len(data), i + WIN)
                            ctx = data[lo:hi]
                            key = f"pid={pid} [{enc}]"
                            if len(hits.setdefault(key, [])) < KEEP_PER_PROC:
                                hits[key].append(ctx.decode("utf-8", "ignore"))
                            for b in BIZ_RE.findall(ctx):
                                bs = b.decode("ascii", "ignore")
                                biz_counter[bs] = biz_counter.get(bs, 0) + 1
                off += CHUNK
                del buf
        if nxt <= addr:
            break
        addr = nxt
    k32.CloseHandle(h)
    return total, n_hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="要找的公众号名，如 「哥飞」")
    ap.add_argument("--out", default="ctx_hits.json")
    args = ap.parse_args()

    needles = make_needles(args.name)
    hits, biz_counter, grand = {}, {}, 0
    for proc in ("Weixin.exe", "WeChatAppEx.exe"):
        for pid in pids_by_name(proc):
            tot, nh = scan(pid, needles, hits, biz_counter)
            grand += nh
            print(f"{proc} pid={pid}: {tot/1024/1024:.0f}MB，命中 {nh}", flush=True)

    print(f"\n总命中 {grand}")
    print("\n== 命中上下文里出现的 biz（按次数排序，第一个通常是目标号）==")
    for b, c in sorted(biz_counter.items(), key=lambda x: -x[1])[:15]:
        print(f"  {b:32s} {c}")

    out = {}
    for k, v in hits.items():
        cleaned = []
        for c in v:
            cleaned.append({
                "biz_nearby": sorted(set(re.findall(r"__biz=([A-Za-z0-9+/=%\-]{10,40})", c))),
                "urls": URL_RE.findall(c)[:6],
                "text": re.sub(r"\s+", " ", c)[:600],
            })
        out[k] = cleaned
    pathlib.Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已存:", args.out)


if __name__ == "__main__":
    main()
