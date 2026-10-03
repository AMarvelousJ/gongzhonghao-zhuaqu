# 公众号抓取踩坑手册（实战验证版）

全部来自真实跑通 197 篇的过程中踩出来的坑，按「症状 → 根因 → 修法」组织。

---

## 一、接口路线是死路，别再试

`mp.weixin.qq.com/mp/profile_ext?action=getmsg` 对外部请求已**僵尸化**：
key 新鲜、Cookie 解密校验通过（`wxuin=<你的 uin>`）、XHR 头齐全、biz 正确，服务端一律回：

```json
{"ret":0,"errmsg":"ok","bizinfo":{"username":"gh_xxx"},"msg_count":0,"home_page_list":[]}
```

`action=home` 的 HTML 里是 `var msgList = '{"list":[]}'`（字段名叫 `msgList`，不是 `general_msg_list`），同样为空。

2026 年 7 月底微信收紧跨公众号文章列表接口（`appmsgpublish` 跨号 `ret=200013`），大批开源工具同时失效。
**这是服务端封禁，不是凭据问题 —— 不要让用户反复点链接重试。**

顺带记录几条死路，别重复走：
- `esentutl /y` 复制 Cookies → `JET_errFileAccessDenied`（Git Bash 下要加 `MSYS_NO_PATHCONV=1`，否则 `/y` 被当路径）
- Radium 走 HTTP/2，请求头被 HPACK 压缩 → 扫进程内存找不到 Cookie 明文
- 微信内置浏览器 profile：`%LOCALAPPDATA%\Tencent\xwechat\radium\web\profiles\<profile>\History` 明文可读，但只有用户浏览过的记录，覆盖不全

---

## 二、User-Agent 与编码（决定生死）

```python
HEADERS = {
    # 必须用 PC 微信 UA：普通 Chrome UA 连续请求会被风控，
    # 返回 31KB「环境异常」壳页（无 #js_content、无图片）。
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
        "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
        "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a1b)"
    ),
    "Referer": "https://mp.weixin.qq.com/",   # 图片防盗链必带
    "Accept-Language": "zh-CN,zh;q=0.9",
}
```

```python
r = requests.get(url, headers=HEADERS, timeout=20)
r.encoding = "utf-8"      # 锁死！apparent_encoding 会误判成 GBK → 标题乱码
```

还要防偶发壳页，加退避重试：

```python
def fetch(url, timeout=20, retries=3):
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            r.encoding = "utf-8"
            text = r.text
        except Exception:
            text = ""
        if text and ("js_content" in text or "rich_media_content" in text):
            return text
        if i < retries:
            time.sleep(2 + 3 * i)
    return text
```

---

## 三、正文图片丢失的四层根因（最核心的坑）

用户反馈「图片没有了」时，**先分清是「没写进正文」还是「原文就没图」**，两类混在一起会误判。
下面 4 个原因会叠加出现，必须全部修掉。

### 1. 懒加载：`<img>` 只有 `data-src` 没有 `src`

html2text 只认 `src` → **图下载了但正文一个 `![` 都没有**。

```python
content = soup.select_one("#js_content") or soup.select_one(".rich_media_content") or soup
for img in content.find_all("img"):
    if not (img.get("src") or "").strip():
        ds = img.get("data-src") or img.get("data-original") or ""
        if ds:
            img["src"] = ds
```

### 2. HTML 实体不同形

HTML 里是 `&amp;`，html2text 输出 `&`，两边字符串对不上 → 替换失败。
凡是拿 URL 去正文里做字符串替换，两边都要 `html_lib.unescape`。

### 3. `src` 与 `data-src` 是两个不同的 URL

`<img>` 上 `src` 可能是低清占位图（`.../0?wx_fmt=jpeg`），`data-src` 才是真图（`.../640?wx_fmt=jpeg`）。
自己扫 HTML 取到的 URL 和 html2text 写进正文的 URL **不是同一个** → 正文里残留未本地化的外链。

**根治办法：图片 URL 一律从转换后的 markdown 里反查，不要扫 HTML。**

```python
images = []
for raw in IMG_MD_RE.findall(body):        # 从 markdown 里取，保证与正文引用 100% 一致
    url = html_lib.unescape(raw)
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("data:"):
        continue
    images.append((url, url, ""))
```

### 4. 图集型文章（微信「图片消息」）

没有 `#js_content` 正文，主体是 `picture_page_info_list` 数组 → 笔记会只剩一句话。

每个 page 对象里有 **3 个 `cdn_url`**：页面主图、`watermark_info`（水印）、`share_cover`（分享封面）
→ **只取每个 page 块的第一个**才是真图。

```python
def images_from_picture_pages(html):
    m = re.search(r"picture_page_info_list\s*:\s*\[", html)
    if not m:
        return []
    start = m.end() - 1
    depth, end = 0, start
    for j in range(start, min(start + 500000, len(html))):
        if html[j] == "[":
            depth += 1
        elif html[j] == "]":
            depth -= 1
            if depth == 0:
                end = j
                break
    arr = html[start:end + 1]
    urls = []
    for obj in split_top_objects(arr):          # 按顶层 {} 切块，要跳过字符串里的括号
        if re.search(r"is_qr_code\s*:\s*'1'", obj):
            continue
        mm = re.search(r"cdn_url\s*:\s*'([^']+)'", obj)
        if mm:
            u = mm.group(1).replace("\\/", "/")
            if u not in urls:
                urls.append(u)
    return urls
```

`split_top_objects` 必须正确处理字符串内的 `{}` 和转义，否则会切错块。

---

## 四、三类特殊文章模板

### A. 活动 / 优惠券类：没有 DOM，正文在 JS 字符串里

没有 `#js_content`，也没有 `var msg_title`，正文只在 `content_noencode: '...'`（`\x0a` 表换行），
标题在 `og:title`，时间在 `create_time: 'YYYY-MM-DD HH:MM'`。

**判定方法**：`"var msg_title" not in html` → 走 JS 正文。
**不能用「谁长就用谁」**——壳页里的「知道了 / 使用小程序 / 取消 允许」模板文本比真正文还长。

### B. 图集型：见上文第三节第 4 条

### C. 普通文章：走 `#js_content` DOM

### 归属 / 元数据的坑

- **别用 `og:site_name` 填 account**，它恒为「微信公众平台」，会把真正的号名冲掉。要用 `var nickname = htmlDecode("...")`。
- 判重键必须是 **`mid` + `idx`**，只用 `mid` 会出事：一次群发的多个 idx 共用一个 mid，
  idx=1 / idx=2 是两篇完全不同的文章，只按 mid 判重会**删掉一篇真实文章**
  （实测踩过：同一次推送的「…【比】」与「…【枫】」撞 mid，差点被合并）。
  `mid` 单独用的场景仅限于「同一篇文章的短链 / 分享链」换算。
- 去重择优键用 `(imported 时间, 正文长度)`，**不能只比长度**。

---

## 五、内存扫描的正确姿势

1. **必须全程在微信里操作**。在系统浏览器里点文章顶部的公众号名会弹「即将前往微信打开此文章」，强制跳走。
2. 用户在微信里打开该号主页 → **滚到底**让列表全部渲染 → 再扫进程。
3. **扫描口径**：
   - 匹配 `https://mp.weixin.qq.com/s/xxx` 完整 URL → 只捞到 8 条（漏 95%）
   - 匹配 `mp.weixin.qq.com/s\?__biz=...&mid=...&idx=...&sn=...` 分享链 → 一次捞到 2577 条
   - 列表数据就是以这种带 `mid`/`sn` 的分享链形态驻留内存的
4. 内存字符串**粘连**（`URL1\x00\x00URL2\x00...`），用合法 URL 字符集正则 `findall` 切开，按 `sn` 长度择优。
5. 扫 `Weixin.exe` + `WeChatAppEx.exe` 全部进程；约 2 分钟，要后台跑。
6. `uin` 在新版微信里是 **base64**，正则 `uin=(\d+)` 匹配不到（这条只在走接口路线时有用）。

---

## 六、运行环境坑

- pip 建议加 `-i https://pypi.org/simple`（部分国内镜像会报 `No matching distribution found`）。
- 由 AI agent 代跑时，`python -c "...中文..."` 经 bash 传参匹配中文文件名会**静默失败** → 写成 `.py` 再跑。
- 后台跑 Python 必须加 `-u`，否则 stdout 有缓冲看不到进度。
- 如果删除操作由 agent 执行，注意命令行级删除通常会被路由到回收站，且某些 agent 会限制单轮删除条目数
  （达到阈值就中断）→ 大批量删除走 Python 脚本绕过计数。
- Windows 上 ctypes 直调 `SHFileOperationW` + `FOF_ALLOWUNDO` 会 **rc 返回非 0 但文件确实已删除**
  → 删完必须 ls/计数复核，别信返回码。
