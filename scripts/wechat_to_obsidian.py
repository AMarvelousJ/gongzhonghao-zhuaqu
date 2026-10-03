#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把微信公众号文章批量灌进 Obsidian。

两种输入源：
  1) wechatDownload 的导出目录（推荐，它能拿到完整历史文章）
     python wechat_to_obsidian.py --src "/tmp/wechat_export/公众号名" --vault "/path/to/YourVault" --account "公众号名"
  2) 一个 URL 列表文件（每行一个 mp.weixin.qq.com 链接）
     python wechat_to_obsidian.py --urls urls.txt --vault ... --account ...

产出：
  <vault>/Resources/<公众号名>/<标题>.md        文章笔记，带 YAML frontmatter
  <vault>/Resources/<公众号名>/attachments/     文章图片（本地化）
  <vault>/Resources/<公众号名>/00-索引.md        按日期倒序的文章索引

图片策略 --images：
  local  (默认) 图片全部落到本地 attachments/，笔记内改为相对路径引用
  remote 保留微信外链（省空间，但会失效）
  strip  不要图片
"""

import argparse
import concurrent.futures as cf
import hashlib
import html as html_lib
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

try:
    import html2text
except ImportError:  # 没有也能跑，只是 html 转换差一点
    html2text = None

# ---------------------------------------------------------------- 基础配置

HEADERS = {
    # 必须用微信自己的 UA：普通 Chrome UA 在连续请求时会被风控，
    # 返回 31KB 的「环境异常」壳页（无 #js_content、无图片）。
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
        "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
        "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a1b)"
    ),
    # 微信图片有防盗链，必须带 Referer
    "Referer": "https://mp.weixin.qq.com/",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 微信公众号正文里的各种垃圾模板文本
JUNK_PATTERNS = [
    r"^\s*点击上方.*?关注.*?$",
    r"^\s*点击.*?蓝字.*?$",
    r"^\s*长按识别二维码.*?$",
    r"^\s*扫描(上方|下方)?二维码.*?$",
    r"^\s*微信扫一扫.*?$",
    r"^\s*预览时标签不可点\s*$",
    r"^\s*轻点两下取消赞\s*$",
    r"^\s*轻点两下取消在看\s*$",
    r"^\s*继续滑动看下一个\s*$",
    r"^\s*喜欢此内容的人还喜欢\s*$",
    r"^\s*(分享|收藏|点赞|在看|赞|在看)\s*$",
    r"^\s*写留言\s*$",
    r"^\s*阅读原文\s*$",
    r"^\s*点击阅读原文\s*$",
    r"^\s*免责声明.*?$",
    r"^\s*本文.*?仅代表作者观点.*?$",
    r"^\s*-{3,}\s*$",
]
JUNK_RE = [re.compile(p, re.M) for p in JUNK_PATTERNS]

ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]')
DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})")
IMG_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
IMG_HTML_RE = re.compile(r'<img[^>]+?(?:src|data-src)=["\']([^"\']+)["\']', re.I)


def log(msg):
    print(msg, flush=True)


def clean_filename(name: str, maxlen: int = 80) -> str:
    name = ILLEGAL_CHARS.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:maxlen].strip() or "untitled"


# ---------------------------------------------------------------- 抓取 / 下载


def fetch(url: str, timeout: int = 20, retries: int = 3) -> str:
    """抓文章页。微信偶尔会返回「环境异常」壳页（约 31KB、无 #js_content），
    出现时退避重试，避免整批退化成空白笔记。"""
    last = ""
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            # 文章页恒为 UTF-8；用 apparent_encoding 猜会误判成 GBK 导致标题乱码
            r.encoding = "utf-8"
            text = r.text
        except Exception:
            text = ""
        last = text
        if text and ("js_content" in text or "rich_media_content" in text):
            return text
        if i < retries:
            time.sleep(2 + 3 * i)
    return last


def download_image(url: str, dest: Path, retries: int = 2) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, stream=True)
            if r.status_code == 200 and r.content:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
                return True
        except Exception:
            pass
        time.sleep(0.5 * (i + 1))
    return False


def guess_ext(url: str, content_type: str = "") -> str:
    ct = (content_type or "").lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/svg+xml": ".svg",
    }
    if ct in mapping:
        return mapping[ct]
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    return ext if ext and len(ext) <= 5 else ".jpg"


# ---------------------------------------------------------------- 正文提取


def html_to_markdown(html: str) -> str:
    if html2text is None:
        soup = BeautifulSoup(html, "lxml")
        return soup.get_text("\n\n")
    h = html2text.HTML2Text()
    h.body_width = 0
    h.ignore_links = False
    h.ignore_images = False
    h.protect_links = True
    h.single_line_break = False
    return h.handle(html)


def strip_junk(text: str) -> str:
    for rx in JUNK_RE:
        text = rx.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_meta_from_html(soup: BeautifulSoup) -> dict:
    def txt(sel):
        el = soup.select_one(sel)
        return el.get_text(strip=True) if el else ""

    meta = {
        "title": txt("#activity-name") or txt("h1") or txt("title"),
        "account": txt("#js_name") or txt(".profile_nickname"),
        "author": txt("#js_author_name") or txt(".rich_media_meta_text"),
        "date": txt("#publish_time"),
    }
    # og:title（优惠券/活动类文章没有 DOM，只有 og + JS 变量）
    # 注意别用 og:site_name —— 它恒为「微信公众平台」，会把真正的号名冲掉
    el = soup.find("meta", property="og:title")
    if el and el.get("content") and not meta.get("title"):
        meta["title"] = el["content"].strip()
    el = soup.find("meta", attrs={"name": "author"})
    if el and el.get("content") and not meta.get("author"):
        meta["author"] = el["content"].strip()

    # 页面里的 var 变量通常更靠谱
    raw = soup.decode()
    for key, pat in [
        ("date", r'var\s+ct\s*=\s*"?(\d{9,})"?'),
        ("date", r'var\s+create_time\s*=\s*"([^"]+)"'),
        ("date", r'create_time:\s*[\'"](\d{4}-\d{2}-\d{2}[^"\']*)[\'"]'),
        ("title", r'var\s+msg_title\s*=\s*\'([^\']*)\''),
        ("account", r'var\s+nickname\s*=\s*"([^"]+)"'),
        ("nickname", r'var\s+nickname\s*=\s*htmlDecode\("([^"]+)"\)'),
    ]:
        if not meta.get(key):
            m = re.search(pat, raw)
            if m:
                v = m.group(1)
                if key == "date" and v.isdigit():
                    meta["date"] = datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d")
                else:
                    meta[key] = v.replace("\\/", "/").strip()
    if not meta.get("account") and meta.get("nickname"):
        meta["account"] = meta.pop("nickname")
    else:
        meta.pop("nickname", None)
    return meta


# 优惠券 / 活动类文章没有 #js_content DOM，正文只以 JS 字符串形式存在
CONTENT_JS_RE = re.compile(r"content_noencode:\s*'((?:[^'\\]|\\.)*)'")


def js_unescape(s: str) -> str:
    return (s.replace("\\x0a", "\n").replace("\\n", "\n")
             .replace("\\t", "\t").replace("\\'", "'").replace('\\"', '"')
             .replace("\\\\", "\\"))


def body_from_js_content(html: str) -> str:
    m = CONTENT_JS_RE.search(html)
    if not m:
        return ""
    text = js_unescape(m.group(1))
    # 纯文本正文：按空行分段，保留原有换行
    paras = [p.strip("\n") for p in re.split(r"\n\s*\n", text) if p.strip()]
    return "\n\n".join(paras) + "\n"


# ---------------------------------------------------------------- 文章对象


class Article:
    def __init__(self, title="", body="", date="", author="", account="",
                 source="", images=None):
        self.title = title
        self.body = body
        self.date = date
        self.author = author
        self.account = account
        self.source = source
        self.images = images or []  # [(原始引用字符串, 本地绝对路径或远程URL, 建议文件名)]

    def __repr__(self):
        return f"<Article {self.date} {self.title[:20]} imgs={len(self.images)}>"


def normalize_date(s: str) -> str:
    if not s:
        return ""
    m = DATE_RE.search(str(s))
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    return ""


def split_top_objects(arr: str):
    """把 JS 数组字符串按顶层 {} 切成若干对象块（跳过字符串里的括号）。"""
    objs, depth, buf = [], 0, []
    i, n = 0, len(arr)
    while i < n:
        c = arr[i]
        if c in "'\"":
            q = c
            buf.append(c)
            i += 1
            while i < n:
                buf.append(arr[i])
                if arr[i] == "\\" and i + 1 < n:
                    buf.append(arr[i + 1])
                    i += 2
                    continue
                if arr[i] == q:
                    i += 1
                    break
                i += 1
            continue
        if c == "{":
            depth += 1
            if depth == 1:
                buf = []
            else:
                buf.append(c)
        elif c == "}":
            depth -= 1
            if depth == 0:
                objs.append("".join(buf))
                buf = []
            else:
                buf.append(c)
        elif depth >= 1:
            buf.append(c)
        i += 1
    return objs


def images_from_picture_pages(html: str) -> list:
    """图集型文章（微信「图片消息」）没有 #js_content 正文，主体是
    picture_page_info_list 数组。每个 page 对象里含 3 个 cdn_url：
    页面主图、watermark_info（水印）、share_cover（分享封面），
    只取每个 page 块里的第一个才是真正要的图。
    """
    m = re.search(r"picture_page_info_list\s*:\s*\[", html)
    if not m:
        return []
    start = m.end() - 1
    depth, end = 0, start
    limit = min(start + 500000, len(html))
    for j in range(start, limit):
        if html[j] == "[":
            depth += 1
        elif html[j] == "]":
            depth -= 1
            if depth == 0:
                end = j
                break
    arr = html[start:end + 1]
    urls = []
    for obj in split_top_objects(arr):
        if re.search(r"is_qr_code\s*:\s*'1'", obj):
            continue
        mm = re.search(r"cdn_url\s*:\s*'([^']+)'", obj)
        if not mm:
            continue
        u = mm.group(1).replace("\\/", "/")
        if u not in urls:
            urls.append(u)
    return urls


def article_from_html_file(path: Path) -> Article:
    html = path.read_text(encoding="utf-8", errors="ignore")
    return article_from_html(html, fallback_title=path.stem, source="")


def article_from_html(html: str, fallback_title: str = "", source: str = "") -> Article:
    soup = BeautifulSoup(html, "lxml")
    meta = extract_meta_from_html(soup)
    content = soup.select_one("#js_content") or soup.select_one(".rich_media_content") or soup

    # 微信图片是懒加载：<img> 只有 data-src 没有 src，html2text 只认 src，
    # 不补的话正文里一张图都不会出现（图片却照常下载了）。
    for img in content.find_all("img"):
        if not (img.get("src") or "").strip():
            ds = img.get("data-src") or img.get("data-original") or ""
            if ds:
                img["src"] = ds

    body = html_to_markdown(str(content))
    body = strip_junk(body)
    # 优惠券/活动类文章没有 #js_content DOM，连 var msg_title 都没有，
    # 正文只以 JS 字符串 content_noencode 存在。DOM 里剩下的是
    # 「知道了 / 使用小程序 / 取消 允许」这类模板文本，比真正文还长，
    # 所以不能比长度，要用 msg_title 缺席来判定。
    js_body = body_from_js_content(html)
    is_js_template = bool(js_body) and "var msg_title" not in html
    if is_js_template or len(body.strip()) < 40:
        body = strip_junk(js_body) if js_body else body

    # 图集型文章（微信「图片消息」）：DOM 里没有正文图，主体是图片数组，
    # 要把图片补进正文，否则笔记只剩一句话。
    pic_urls = images_from_picture_pages(html)
    if pic_urls:
        add = [u for u in pic_urls if f"]({u})" not in body]
        if add:
            body = body.rstrip() + "\n\n" + "\n\n".join(f"![图片]({u})" for u in add) + "\n"

    # 图片 URL 一律从转换后的 markdown 里取，保证和正文里的引用字符串完全一致。
    # （自己扫 HTML 会撞上 src / data-src / 其它 *src 属性，同一张图的占位图与
    #   真图 URL 不同，扫到的和 html2text 写进正文的对不上，替换就会漏，
    #   正文里留下没本地化的外链。）
    images = []
    for raw in IMG_MD_RE.findall(body):
        url = html_lib.unescape(raw)
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("data:"):
            continue
        images.append((url, url, ""))

    return Article(
        title=meta.get("title") or fallback_title,
        body=body,
        date=normalize_date(meta.get("date", "")),
        author=meta.get("author", ""),
        account=meta.get("account", ""),
        source=source,
        images=images,
    )


def article_from_md(text: str, fallback_title: str = "", source: str = "",
                    base_dir: Path = None) -> Article:
    """解析 wechatDownload 之类导出的 markdown。"""
    date = ""
    title = ""
    body = text

    # 去掉已有 frontmatter
    fm = {}
    if body.lstrip().startswith("---"):
        parts = body.split("---", 2)
        if len(parts) >= 3:
            raw_fm = parts[1]
            for line in raw_fm.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip().lower()] = v.strip().strip('"')
            body = parts[2]

    # 标题：第一个一级标题
    m = re.search(r"^#\s+(.+)$", body, re.M)
    if m:
        title = m.group(1).strip()
        body = body[: m.start()] + body[m.end():]

    # 日期：文章开头附近找日期串
    head = body[:400]
    date = normalize_date(head) or normalize_date(fm.get("date", "")) or normalize_date(fm.get("created", ""))

    # 来源链接
    src = fm.get("source", "") or source
    m = re.search(r"(https?://mp\.weixin\.qq\.com/\S+)", text)
    if not src and m:
        src = m.group(1)

    account = fm.get("nickname", "") or fm.get("account", "")

    # 图片引用
    images = []
    for m in IMG_MD_RE.finditer(body):
        ref = m.group(1)
        if ref.startswith("data:"):
            continue
        if ref.startswith("http"):
            images.append((ref, ref, guess_ext(ref)))
        else:
            p = (base_dir / ref) if base_dir else Path(ref)
            if p.exists():
                images.append((ref, str(p.resolve()), p.suffix.lower()))

    body = strip_junk(body)

    return Article(
        title=title or fallback_title,
        body=body,
        date=date,
        author=fm.get("author", ""),
        account=account,
        source=src,
        images=images,
    )


# ---------------------------------------------------------------- 导入流程


def collect_source_files(src: Path):
    files = []
    for p in sorted(src.rglob("*")):
        if p.is_file() and p.suffix.lower() in (".md", ".html", ".htm"):
            files.append(p)
    return files


def build_output(article: Article, vault: Path, account: str,
                 attach_dir: Path, image_mode: str, counter: dict) -> Path:
    """生成一篇 Obsidian 笔记，返回路径。"""
    target_dir = vault / "Resources" / clean_filename(account)
    target_dir.mkdir(parents=True, exist_ok=True)

    fname = clean_filename(article.title) + ".md"
    out = target_dir / fname
    n = 2
    while out.exists():
        out = target_dir / f"{clean_filename(article.title)} ({n}).md"
        n += 1

    body = article.body

    # ---- 图片处理
    rel_attach = None
    if image_mode != "strip" and article.images:
        rel_attach = attach_dir.relative_to(target_dir) if attach_dir.is_relative_to(target_dir) \
            else Path(attach_dir.name)
        attach_dir.mkdir(parents=True, exist_ok=True)

        # 并行下载远程图
        jobs = []
        for ref, loc, ext in article.images:
            if loc.startswith("http"):
                jobs.append((ref, loc, ext))
        if jobs:
            def _job(item):
                ref, url, ext = item
                safe = hashlib.md5(url.encode("utf-8")).hexdigest()[:10]
                dest = attach_dir / f"{safe}{ext or guess_ext(url)}"
                ok = download_image(url, dest)
                return ref, (dest if ok else None)
            with cf.ThreadPoolExecutor(max_workers=8) as ex:
                results = dict(r for r in ex.map(_job, jobs) if r)
        else:
            results = {}

        for ref, loc, ext in article.images:
            if loc.startswith("http"):
                dest = results.get(ref)
                if dest is None:
                    if image_mode == "remote":
                        continue  # 保留原外链
                    body = body.replace(f"]({ref})", f"]({ref}) <!-- 图片下载失败 -->")
                    counter["img_fail"] += 1
                    continue
                new = f"{rel_attach.as_posix()}/{dest.name}"
            else:
                srcp = Path(loc)
                dest = attach_dir / f"{hashlib.md5(str(srcp).encode('utf-8')).hexdigest()[:10]}{srcp.suffix.lower()}"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy2(srcp, dest)
                counter["img_local"] += 1
                new = f"{rel_attach.as_posix()}/{dest.name}"
            if image_mode == "local":
                body = body.replace(f"]({ref})", f"]({new})")
                counter["img_ok"] += 1

    # ---- frontmatter
    fm_lines = ["---"]
    fm_lines.append(f"title: \"{article.title.replace(chr(34), chr(39))}\"")
    if article.account:
        fm_lines.append(f"account: \"{article.account}\"")
    if article.date:
        fm_lines.append(f"date: {article.date}")
    if article.author:
        fm_lines.append(f"author: \"{article.author}\"")
    if article.source:
        fm_lines.append(f"source: {article.source}")
    fm_lines.append("type: 公众号文章")
    fm_lines.append(f"tags: [公众号/{account}]")
    fm_lines.append(f"imported: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    fm_lines.append("---")

    out.write_text("\n".join(fm_lines) + "\n\n" + body.strip() + "\n", encoding="utf-8")
    return out


def write_index(vault: Path, account: str, entries):
    target_dir = vault / "Resources" / clean_filename(account)
    target_dir.mkdir(parents=True, exist_ok=True)
    entries = sorted(entries, key=lambda e: e[1] or "0000-00-00", reverse=True)
    lines = [
        "---",
        f"title: \"{account} 文章索引\"",
        "type: 索引",
        f"tags: [公众号/{account}]",
        f"updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "---",
        "",
        f"# {account} 文章索引",
        "",
        f"共 {len(entries)} 篇。",
        "",
    ]
    for path, date in entries:
        label = f"{date} " if date else ""
        lines.append(f"- {label}[[{path.stem}]]")
    (target_dir / "00-索引.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target_dir / "00-索引.md"


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description="公众号文章 → Obsidian 笔记")
    ap.add_argument("--src", help="wechatDownload 导出目录")
    ap.add_argument("--urls", help="文章 URL 列表文件（每行一个）")
    ap.add_argument("--vault", required=True, help="Obsidian 库根目录")
    ap.add_argument("--account", help="公众号名称（用作文件夹名，缺省自动推断）")
    ap.add_argument("--images", choices=["local", "remote", "strip"], default="local")
    ap.add_argument("--attach-dir", help="图片存放目录，默认 <vault>/Resources/<公众号>/attachments")
    ap.add_argument("--delay", type=float, default=0.8, help="抓取间隔秒（仅 --urls 模式）")
    args = ap.parse_args()

    vault = Path(args.vault)
    if not vault.is_dir():
        sys.exit(f"Obsidian 库不存在: {vault}")
    if not args.src and not args.urls:
        sys.exit("需要 --src 或 --urls 之一")

    account = args.account or ""
    attach_dir = Path(args.attach_dir) if args.attach_dir else None

    counter = {"img_ok": 0, "img_fail": 0, "img_local": 0}
    entries = []

    # ---------- 模式 1：本地导出目录
    if args.src:
        src = Path(args.src)
        if not src.is_dir():
            sys.exit(f"导出目录不存在: {src}")
        if not account:
            account = src.name
        files = collect_source_files(src)
        log(f"扫描到 {len(files)} 个文件")

        for i, p in enumerate(files, 1):
            try:
                if p.suffix.lower() == ".md":
                    art = article_from_md(
                        p.read_text(encoding="utf-8", errors="ignore"),
                        fallback_title=p.stem, base_dir=p.parent,
                    )
                else:
                    art = article_from_html_file(p)
            except Exception as e:
                log(f"  [跳过] {p.name}: {e}")
                continue
            if not art.title:
                art.title = p.stem
            if account:
                art.account = art.account or account

            if attach_dir is None:
                attach_dir = vault / "Resources" / clean_filename(account) / "attachments"
            out = build_output(art, vault, account, attach_dir, args.images, counter)
            entries.append((out, art.date))
            if i % 20 == 0 or i == len(files):
                log(f"  进度 {i}/{len(files)}")

    # ---------- 模式 2：URL 列表
    else:
        urls = [u.strip() for u in Path(args.urls).read_text(encoding="utf-8").splitlines()
                if u.strip() and u.strip().startswith("http")]
        log(f"待抓取 {len(urls)} 篇")
        if not account:
            account = "公众号文章"
        if attach_dir is None:
            attach_dir = vault / "Resources" / clean_filename(account) / "attachments"

        for i, url in enumerate(urls, 1):
            try:
                art = article_from_html(fetch(url), source=url)
            except Exception as e:
                log(f"  [失败] {url}: {e}")
                continue
            if not art.title:
                art.title = f"未命名-{i}"
            if account:
                art.account = art.account or account
            out = build_output(art, vault, account, attach_dir, args.images, counter)
            entries.append((out, art.date))
            log(f"  [{i}/{len(urls)}] {art.date} {art.title[:30]}")
            time.sleep(args.delay)

    idx = write_index(vault, account, entries)
    log("")
    log(f"完成：{len(entries)} 篇 → {vault/'Resources'/clean_filename(account)}")
    log(f"图片：本地 {counter['img_local']} / 下载成功 {counter['img_ok']} / 失败 {counter['img_fail']}")
    log(f"索引：{idx}")


if __name__ == "__main__":
    main()
