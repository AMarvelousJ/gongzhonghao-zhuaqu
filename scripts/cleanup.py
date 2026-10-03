#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把确认无用的文件送进 Windows 回收站（可恢复），并留一份完整清单。

用法:
    # 1) 先由 orphan_check.py 产出 orphan_list.json
    python cleanup.py --dir "<vault>/Resources/<公众号名>" \
                      --orphan-list orphan_list.json \
                      --extra "<vault>/Resources/xxx_备份" \
                      --extra "<vault>/Resources/某篇旧稿.md" \
                      --list-out deleted_files.txt            # 只出清单，不删
    # 2) 确认清单无误后加 --yes 真正执行

注意：本机 SHFileOperationW 会出现「返回码非 0 但文件确实已删除」的情况，
      执行后必须用 ls/计数复核，不要只看脚本返回的成功批次数。
"""
import argparse
import ctypes
import json
import pathlib
import sys
from ctypes import wintypes

if sys.platform != "win32":
    sys.exit(
        "[不支持] cleanup.py 通过 ctypes 调 Windows Shell API（SHFileOperationW）把文件送进回收站，"
        "只能在 Windows 上运行。\n当前平台：%s。\n"
        "在 macOS / Linux 上请改用命令行工具：\n"
        "  macOS: trash（brew install trash）\n"
        "  Linux: gio trash <文件>  或  trash-put <文件>" % sys.platform
    )


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


FO_DELETE = 3
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMATION = 0x0010
FOF_NOERRORUI = 0x0400
FOF_SILENT = 0x0004

shfileop = ctypes.windll.shell32.SHFileOperationW
shfileop.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]


def to_recycle_bin(paths):
    ok, failed = 0, []
    BATCH = 40
    for i in range(0, len(paths), BATCH):
        chunk = paths[i:i + BATCH]
        buf = "\0".join(str(p) for p in chunk) + "\0\0"
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = buf
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT
        rc = shfileop(ctypes.byref(op))
        if rc == 0 and not op.fAnyOperationsAborted:
            ok += 1
        else:
            failed.extend(chunk)
    return ok, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="公众号笔记目录（含 attachments/）")
    ap.add_argument("--orphan-list", help="orphan_check.py 产出的 json")
    ap.add_argument("--extra", action="append", default=[],
                    help="额外要清理的文件或目录（可重复）")
    ap.add_argument("--list-out", default="deleted_files.txt")
    ap.add_argument("--yes", action="store_true", help="真正执行；不加只出清单")
    args = ap.parse_args()

    targets = []
    if args.orphan_list:
        ol = json.loads(pathlib.Path(args.orphan_list).read_text(encoding="utf-8"))
        base = pathlib.Path(args.dir) / "attachments"
        targets += [base / n for n in ol.get("dup", []) + ol.get("unique", [])]
    dirs = []
    for e in args.extra:
        p = pathlib.Path(e)
        if p.is_dir():
            dirs.append(p)
            targets += [x for x in sorted(p.rglob("*"), key=lambda q: len(q.parts),
                                          reverse=True) if x.is_file()]
        elif p.exists():
            targets.append(p)

    targets = [t for t in targets if t.exists()]
    total_mb = sum(t.stat().st_size for t in targets) / 1048576
    print("待回收文件: %d 个 (%.1f MB)" % (len(targets), total_mb))
    if dirs:
        print("  含整个目录:", ", ".join(d.name for d in dirs))

    with open(args.list_out, "w", encoding="utf-8") as f:
        for p in targets:
            f.write("%d\t%s\n" % (p.stat().st_size, p))
    print("清单已写入:", args.list_out)

    if not args.yes:
        print("\n（预览模式，未删除。确认清单后加 --yes 执行）")
        return

    ok_batches, failed = to_recycle_bin([p for p in targets if p.is_file()])
    print("\n已送回收站: %d 批 / 共 %d 文件" % (ok_batches, len(targets)))
    if failed:
        print("报告失败 %d 个（注意：本机可能实际已删，务必复核）:" % len(failed))
        for p in failed[:5]:
            print("   ", p)

    for d in dirs:
        try:
            if d.exists() and not list(d.rglob("*")):
                d.rmdir()
                print("已移除空目录:", d)
        except Exception as e:
            print("移除目录失败:", d, e)

    print("\n完成。建议立即用 ls/计数复核实际结果。")


if __name__ == "__main__":
    main()
