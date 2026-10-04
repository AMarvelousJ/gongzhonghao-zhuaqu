#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【chksm 优先提取器】直接从微信进程内存里挖「带 chksm 的完整分享链」。

什么时候用它
------------
正常流程是 mem_scan_links.py → clean_links.py。但实测有公众号（如「快刀青衣」）
靠 clean_links.py 产出的链接（只有 __biz/mid/idx/sn）**全部**返回 17.7KB 空壳，
必须把原始 URL 里的 `chksm=` 一起带上才能拿到正文。
判断方法：clean_links.py 现在会打印 chksm 覆盖率；若抓出来大批空壳且覆盖率很低，
就改用本脚本重新提一遍链接（不需要重跑 mem_scan_links.py，它自己扫内存）。

它比 mem_scan_links.py 更严格：只收 biz 匹配、sn 为 32 位 hex、且带 chksm 的串，
因此拿到的基本都是干净可用的链接。

用法:
    python extract_chksm_links.py <输出.txt> --biz <biz字符串>

仅 Windows。
"""
import argparse
import ctypes
import pathlib
import re
import sys
from ctypes import wintypes

if sys.platform != "win32":
    sys.exit("[不支持] extract_chksm_links.py 依赖 Windows API 读取进程内存，仅 Windows 可用。")

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


def scan(pid, target, bucket):
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return 0, 0
    pat = re.compile(
        rb"mp\.weixin\.qq\.com/s\?__biz=" + re.escape(target.encode())
        + rb"&mid=(\d+)&idx=(\d+)&sn=([0-9a-f]{32})&chksm=([0-9a-f]{16,80})")
    addr, total, n = 0, 0, 0
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
                    for m in pat.finditer(data):
                        mid, idx, sn, chksm = (g.decode() for g in m.groups())
                        key = (mid, idx)
                        # 同一篇内存里可能有多份副本，取 chksm 最长的那份
                        if key not in bucket or len(chksm) > len(bucket[key][1]):
                            bucket[key] = (sn, chksm)
                        n += 1
                off += CHUNK
                del buf
        if nxt <= addr:
            break
        addr = nxt
    k32.CloseHandle(h)
    return total, n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("dst", help="输出 URL 清单 txt")
    ap.add_argument("--biz", required=True, help='目标 biz，如 "MjM5NjQyMjE1NA=="')
    args = ap.parse_args()

    bucket = {}
    for name in ("Weixin.exe", "WeChatAppEx.exe"):
        for pid in pids_by_name(name):
            tot, n = scan(pid, args.biz, bucket)
            print(f"{name} pid={pid}: {tot/1024/1024:.0f}MB，带chksm命中 {n}", flush=True)

    out = []
    for (mid, idx), (sn, chksm) in bucket.items():
        out.append(f"https://mp.weixin.qq.com/s?__biz={args.biz}&mid={mid}&idx={idx}"
                   f"&sn={sn}&chksm={chksm}")
    out.sort(key=lambda u: int(re.search(r"mid=(\d+)", u).group(1)), reverse=True)
    pathlib.Path(args.dst).write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"\n去重后 {len(out)} 条 -> {args.dst}")
    for u in out[:3]:
        print("  ", u)
