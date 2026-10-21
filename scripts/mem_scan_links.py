#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二轮内存扫描（宽松口径）：
1) 裸路径片段  /s/<22位id>
2) 完整分享链接 s?__biz=..&mid=..&idx=..&sn=..
上次只匹配 https:// 开头，漏掉了列表数据里以纯路径形式存放的链接。
"""
import ctypes
import json
import pathlib
import re
import sys
from ctypes import wintypes

if sys.platform != "win32":
    sys.exit(
        "[不支持] mem_scan_links.py 通过 ctypes 直接调 Windows API（ReadProcessMemory 等）"
        "读取微信进程内存，只能在 Windows 上运行。\n"
        "当前平台：%s。可以改用 skill 里的「路线 B（公众号后台 appmsgpublish）」"
        "或「路线 C（已有 URL 列表）」来获取文章清单。" % sys.platform
    )

k32 = ctypes.windll.kernel32
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x2
MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
READABLE = {0x02, 0x04, 0x20, 0x40}
CHUNK = 8 * 1024 * 1024
OVERLAP = 8192


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


# 22 位左右的文章 id（字符集与实际样本一致），前后不能是单词字符
PATH_RE = re.compile(rb"(?<![A-Za-z0-9_/.-])/s/([A-Za-z0-9_-]{20,24})(?![A-Za-z0-9_-])")
# 完整分享式链接
FULL_RE = re.compile(rb"mp\.weixin\.qq\.com/s\?[^\"'\s<>]{30,300}")


def scan(pid, ids, fulls):
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return 0
    addr, total = 0, 0
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
                n = min(CHUNK + OVERLAP, size - off)
                buf = ctypes.create_string_buffer(n)
                got = ctypes.c_size_t()
                if k32.ReadProcessMemory(h, ctypes.c_void_p(base + off), buf, n, ctypes.byref(got)) and got.value:
                    total += got.value
                    data = buf.raw[:got.value]
                    for m in PATH_RE.finditer(data):
                        ids.add(m.group(1).decode("ascii", "ignore"))
                    for m in FULL_RE.finditer(data):
                        s = m.group(0).decode("ascii", "ignore")
                        if "sn=" in s:
                            fulls.add("https://" + s)
                off += CHUNK
                del buf
        if nxt <= addr:
            break
        addr = nxt
    k32.CloseHandle(h)
    return total


def scan2(pid):
    """返回 (ids, fulls, bytes)，不共享集合，方便并发/多进程。"""
    ids, fulls = set(), set()
    return ids, fulls, scan(pid, ids, fulls)


def _scan2_star(args):
    return scan2(args)


if __name__ == "__main__":
    import argparse
    from concurrent.futures import as_completed
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="mem_links2.json")
    ap.add_argument("--workers", type=int, default=4,
                    help="并发扫描进程数（默认 4）。默认走多进程：正则扫描是 GIL 密集的，"
                         "多线程加速不了，必须多进程")
    ap.add_argument("--threads", action="store_true",
                    help="改用线程池而不是进程池（调试用，通常更慢）")
    ap.add_argument("--biz", default="",
                    help="只保留该 biz 的分享链，其余丢弃（省内存，也方便直接看命中数）")
    args = ap.parse_args()
    out = pathlib.Path(args.out)

    jobs = []
    for name in ("Weixin.exe", "WeChatAppEx.exe"):
        for pid in pids_by_name(name):
            jobs.append((name, pid))

    t0 = time.time()
    ids, fulls = set(), set()
    if args.threads or args.workers <= 1:
        import threading
        from concurrent.futures import ThreadPoolExecutor
        ex = ThreadPoolExecutor(max_workers=max(1, args.workers))
    else:
        from concurrent.futures import ProcessPoolExecutor
        ex = ProcessPoolExecutor(max_workers=max(1, args.workers))
    with ex:
        futs = {ex.submit(_scan2_star, pid): (name, pid) for name, pid in jobs}
        for fut in as_completed(futs):
            name, pid = futs[fut]
            try:
                i, f, b = fut.result()
            except Exception as e:
                print(f"{name} pid={pid}: 失败 {e}", flush=True)
                continue
            ids |= i
            fulls |= f
            print(f"{name} pid={pid}: {b/1024/1024:.0f}MB，id +{len(i)}，完整链 +{len(f)}", flush=True)

    links = sorted({"https://mp.weixin.qq.com/s/" + i for i in ids} | fulls)
    if args.biz:
        b = args.biz
        bq = b.replace("=", "%3D")
        links = [u for u in links if (b in u or bq in u)]
    print(f"\n合计 {len(links)} 条（裸 id {len(ids)} + 分享链 {len(fulls)}），耗时 {time.time()-t0:.0f}s")
    out.write_text(json.dumps(links, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已存:", out)
