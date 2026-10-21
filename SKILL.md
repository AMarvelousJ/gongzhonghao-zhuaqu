---
name: gongzhonghao-zhuaqu
description: 把微信公众号某个博主的历史文章批量抓取并导入 Obsidian（或任何本地 Markdown 库），图片全部本地化、带 YAML frontmatter、自动生成索引。当用户想把公众号文章/博主全部文章下载到 Obsidian、批量保存公众号文章、做公众号文章归档、把微信文章转成 Markdown 笔记时使用。不依赖任何第三方下载器（wechatDownload 等已不可用）。
agent_created: true
---

# 公众号文章抓取 → Obsidian

## 最快用法（一句话，日常就走这条）

```bash
<python> scripts/wechat_sync.py --account "橘AI"          # 增量：只抓新文章 + 更新索引
<python> scripts/wechat_sync.py --account "橘AI" --biz "<biz>" --vault "<库根>"   # 首次
<python> scripts/wechat_sync.py --all                     # 注册表里所有号轮流更新
<python> scripts/wechat_sync.py --list                    # 看看都收录了哪些号
```

`wechat_sync.py` 一条命令跑完「内存扫描 → 清洗 → diff 出新文章 → 并发抓取 → 重建索引」，
biz / vault / 上次同步时间记在 `~/.workbuddy/state/gongzhonghao-zhuaqu/accounts.json`，
**第二次开始不带任何参数也能跑**。

也配了 MCP 服务（`mcp/server.py`，已注册到 `~/.workbuddy/mcp.json` 的
`gongzhonghao-zhuaqu`），Agent 里直接说「更新一下橘AI的公众号文章」即可，
工具见文末「MCP 用法」。

## 用途

把一个公众号的历史文章**全量**抓下来，生成 Obsidian 笔记：每篇一个 `.md`，带 YAML frontmatter（标题/日期/作者/公众号/原文链接），**图片全部下载到本地**并在正文里以相对路径引用，附带按日期倒序的 `00-索引.md`。

不依赖任何第三方下载器。2026 年起微信已封禁跨号文章列表接口，本 skill 走「微信进程内存扫描 + 公开文章页抓取」路线，已实测跑通 197 篇。

## 硬性约束（必读）

1. **User-Agent 必须是 PC 微信 UA**，不能用普通 Chrome UA。普通 UA 连续请求会被风控，全部返回 31KB 的「环境异常」壳页 → 生成一堆空笔记。手机 UA（iPhone/Android）也不行，只返回 17KB 空壳。
2. **编码必须写死 `r.encoding = "utf-8"`**。`apparent_encoding` 会把部分页面误判成 GBK，标题变成 `Õ░▒õĖÜ...` 乱码。
3. **链接里的 `chksm` 参数不能丢**（2026-10-04 踩到）：只带 `__biz/mid/idx/sn` 的链接，
   在部分公众号上**全部**返回 17.7KB 空壳（跟文章被删的表现一模一样，极易误判），
   把 `chksm=` 带上就正常了。`clean_links.py` 已改为保留 chksm；若仍大批空壳，
   改用 `scripts/extract_chksm_links.py` 直接从内存重提（更严格，见 Step 1）。
4. **下载图片必须带 `Referer: https://mp.weixin.qq.com/`**，微信图片有防盗链。
5. **不要用 WeChatMsg（留痕）** —— 那是解微信聊天记录数据库的，与公众号文章无关。
6. **不要让用户去装 wechatDownload**：GitHub 仓库只有 README，是闭源项目，exe 只能从网盘下。
7. 涉及**中文路径**时，不要写 `python -c "...中文..."` 经 bash 传参（会静默失败），一律写成 `.py` 脚本文件再跑。
8. 后台跑 Python 必须加 `-u`（stdout 有缓冲，否则看不到进度）。
9. **JS 兜底正文必须解 `\xHH` 十六进制转义**。没有 `#js_content` DOM 的文章，正文只存在于
   `content_noencode: '...'` 这个 JS 字符串里，其中 `<` `>` `"` 分别写成 `\x3c` `\x3e` `\x22`。
   只解 `\n` / `\t` 会让正文变成一屏 `\x3cp\x3e\x3cspan...`，图片也因为拿不到 `data-src`
   而全部丢失（实测 755 篇里 23 篇中招）。已修在 `js_unescape()` + `body_from_js_content()`。
10. **`og:title` 只在「≤80 字且不含换行」时才可信**（2026-10-05 踩到）：微信「文字消息」
    没有 `var msg_title`，此时 `og:title` 会被塞满**整篇正文**（含字面 `\n\n`），
    直接当标题会生成几百字长、满是「 n 」的文件名（花叔 386 篇中 19 篇）。
    挡掉之后用正文首句提炼。**补救时不要用「标题长」当判定标准** —— 有些标题本来就有
    60+ 字；要回原文重查 `var msg_title`，查得到就还原。已修在 `extract_meta_from_html()`
    + 新增 `title_from_body()`。

## 环境

- **平台：仅 Windows**。`mem_scan_links.py`（进程内存扫描）与 `cleanup.py`（送回收站删除）依赖
  `ctypes.windll` 调 Windows API，macOS / Linux 上这两个脚本不可用（会直接提示退出）；
  其余脚本跨平台。
- Python **3.9+**，依赖见 `requirements.txt`：
  ```bash
  pip install -r requirements.txt   # requests / beautifulsoup4 / lxml / html2text
  ```
  `html2text` 缺失也能跑，只是 HTML→Markdown 的转换质量差一些。
- 装包建议用官方源：`-i https://pypi.org/simple`（某些国内镜像会 `No matching distribution found`）。

## 工作流

### Step 0 · 确认信息

向用户问清：公众号名称、Obsidian 库路径、图片策略（本地/外链）、笔记组织方式。
然后**拿到该号的 biz 字符串**（后面过滤要用）：在微信里打开该号任意一篇文章 → 用浏览器打开 → 页面源码前 2KB 内有
```
var biz = "MzA5NjM4MDAxMg==" || ""     // ← 这个就是 biz，Step 1 过滤要用
var nickname = htmlDecode("示例公众号")
var user_name = "gh_xxxxxxxxxxxx"
```

### Step 1 · 拿全量文章 URL 列表

**路线 A（推荐，零成本）：微信进程内存扫描**

1. 让用户在**微信里**（不是系统 Chrome）打开该号主页 → **滚到底**让全部文章渲染完。
   ⚠️ 在系统浏览器里点文章顶部的公众号名会强制跳回微信，此路不通。
2. 后台跑扫描（**多进程并发，实测 45 秒**；19 个进程 / 约 9GB 内存）：
   ```bash
   <python> -u scripts/mem_scan_links.py mem_links.json --workers 4 --biz "<biz>"
   ```
   ⚠️ 必须用**进程池**不是线程池：瓶颈是在读出来的内存块上跑正则（GIL 密集），
   实测多线程 160s、多进程 45s。`--threads` 可强制退回线程池（调试用）。
   `--biz` 会在写盘前就把别的号的链接丢掉，json 小很多。
3. 清洗去重（`clean_links.py` 用 cwd 相对路径，没问题）：
   ```bash
   <python> scripts/clean_links.py mem_links.json urls_clean.txt --biz "<biz字符串>"
   ```
4. 归属过滤（流式读前 120KB 判定；**默认并发 8**，546 条从 10 分钟压到 1 分钟内）：
   ```bash
   <python> scripts/filter_by_biz.py "$PWD/urls_clean.txt" --biz "<biz>" --name "<公众号名>" \
       --out "$PWD/urls_final.txt" --report "$PWD/candidates_report.json" --workers 8
   ```
   首次全量时可以只抽样：`--sample 30`（其余按 URL 里的 `__biz` 直接放行，抓取阶段
   还有 `--biz` 再兜一道）。增量更新时新链接少，走全量校验。
   ⚠️ 判定是**三态**的，别再加严格判据：只有页面里明确出现「别的 biz」才剔除；
   新版「文字消息」页前 120KB 里连自己的 biz 字符串都没有（实测橘AI 最新两篇），
   一律**判不出来就放行**——宁可多抓一篇，也不能把好文章丢了。
   ⚠️ `filter_by_biz.py` 早期版本会把相对路径拼到**脚本所在目录**，保险起见传绝对路径。

> **不知道 biz 时**，两条路（按可靠性排序）：
>
> 1. **内存上下文扫描（推荐，最快最准）**：`scripts/mem_ctx_scan.py` 直接在微信进程内存里
>    搜公众号名（同时试 UTF-8 / UTF-16LE），把命中位置 ±3KB 范围内出现的 `__biz=` 全统计出来。
>    实测搜「快刀青衣」命中 344 次，其中 `MjM5NjQyMjE1NA==` 占 99 次，一眼就能定，
>    比统计 biz 分布靠谱得多（分布法容易被「用户还开着别的号」干扰）。
>    ```bash
>    <python> -u scripts/mem_ctx_scan.py        # 结果写 ctx_hits.json
>    ```
> 2. **统计 biz 分布**：数 `mem_links.json` 里各 biz 的「唯一 mid+idx 数」，取最大的候选，
>    再抽样抓 5 篇看页面里的 `var nickname = htmlDecode("...")` 确认归属。
>
> ⚠️ **抽样验证时的陷阱**：如果抽样的几篇全部返回 17.7KB 空壳，**先别急着换 biz**——
> 很可能是 chksm 丢了（见硬性约束 3）。判据：拿一条 `clean_links.py` 产出的链接，
> 手工补上从上下文里看到的 `chksm=` 再抓一次，能出正文就证明 biz 是对的，只是链接形态不对。
> 先用一条**已知有效的、别的号的**链接（比如从搜索结果里随便找一篇带 `__biz` 的）验证抓取通道本身正常，
> 再怀疑 biz —— 这一步能省掉大量瞎猜。

> **最快的拿 biz 办法（不用问用户要链接）**：直接 WebSearch `<号名> 公众号 文章`，
> 搜索结果里常能翻到 `mp.weixin.qq.com/s/xxx` 原文链接（或被转载的页面里带的「原文链接」）。
> 拿任一链接跑一遍取 `var biz` / `var nickname` 即可确认。实测找「哥飞」的 biz 只花了一次搜索，
> 比让用户去微信里翻文章快得多。注意：必须先校验页面里的 `nickname` 确实是目标号，别认错号。

> **扫描口径决定成败**：只匹配 `https://mp.weixin.qq.com/s/xxx` 只能捞到 8 条（漏 95%）；
> 必须匹配 `mp.weixin.qq.com/s\?__biz=...&mid=...&idx=...&sn=...` 分享链，一次能捞到 2500+ 条。
> 内存里的字符串是**粘连**的（`URL1\x00\x00URL2\x00...`），按合法 URL 字符集正则切开，同一篇取 `sn` 最长的。

**路线 B（兜底）：用户自己有公众号**
扫码登录 mp.weixin.qq.com 后台 → `searchbiz` 拿 fakeid → `appmsgpublish` 拿全量列表。

**路线 C**：已有 URL 列表或 wechatDownload 导出目录 → 直接进 Step 2。

### Step 2 · 抓取并生成笔记

```bash
<python> -u scripts/wechat_to_obsidian.py \
    --urls urls_final.txt \
    --vault "<Obsidian 库根目录>" \
    --account "<公众号名>" \
    --images local \
    --workers 6 \
    --biz "<biz>"            # 可选：抓到后校验归属，串号的直接丢弃
    --no-index               # 增量时用：不覆盖索引，交给 reindex.py 全量重建
```

导出目录模式用 `--src <目录>` 代替 `--urls`。

**并发实测（橘AI，随机 60 篇 / 399 张图）**：`--workers 6` → 49s，`--workers 10` → 44s
（边际收益很小还更招风控）。改造前串行版 546 篇要 26 分钟，现在约 **7 分钟**。默认就 6。

> ⚠️ `write_index()` 只写**本次**抓的条目 → 增量跑必须加 `--no-index`，否则索引会被覆盖成只剩新文章。
> ⚠️ 并发写盘：`build_output()` 挑文件名那段要用 `_WRITE_LOCK` 锁住并先 `touch()` 占位，
> 否则两个线程会算出同一个文件名。图片下载的内层线程池按 `--workers` 调小
> （`IMG_WORKERS = max(2, 8 // workers)`），不然 6×8=48 线程太炸。

### Step 2.5 · 重建索引（增量更新走这条）

```bash
<python> scripts/reindex.py --dir "<vault>/Resources/<公众号名>" --account "<公众号名>"
```
非破坏性：只按现有笔记重排 `00-索引.md`，**不删任何东西**。日常增量用它，
比每次都跑 `finalize.py` 安全（finalize 会去重删除，见下）。

产物：
```
<vault>/Resources/<公众号名>/<标题>.md
<vault>/Resources/<公众号名>/attachments/
<vault>/Resources/<公众号名>/00-索引.md
```

### Step 3 · 收尾去重（只在需要时跑；日常增量用 reindex.py 就够了）

同一篇文章可能以 `/s/<id>` 短链和分享链两种形态同时入库，会留下 `xxx (2).md` 副本：
```bash
<python> scripts/finalize.py --dir "<vault>/Resources/<公众号名>" --account "<公众号名>"
<python> scripts/finalize.py --dir "..." --account "..." --apply
```
（先预览再加 `--apply`；择优键是 `(imported 时间, 正文长度)`，**不能只比长度**，抓坏的模板文本可能更长）

> ⚠️ 已知副作用：哥飞那批里，finalize 的二次去重（标题 + 正文前 150 字指纹）误删了 4 篇
> 「同一活动通知隔几天重发」的真文章 —— 它们标题和开头完全一致。要保留就得去掉 2c 那一段。
> 已修：`finalize.py` 改名后必须把新路径写回 `by_src`，否则 2c 会 unlink 一个已被 rename 掉的
> 旧路径 → FileNotFoundError 崩溃（实测触发）。

### Step 4 · 图片体检（必做）

```bash
<python> scripts/check_images.py --dir "<vault>/Resources/<公众号名>"
```
合格标准：**「引用了但磁盘上没有」= 0**，且「正文含裸 `<img>` 标签」= 0。

有残留外链时：
```bash
<python> scripts/fix_remote.py --dir "<vault>/Resources/<公众号名>"
```

⚠️ 上述两个脚本都只认**微信图床**。少数老文章会直接贴外部图床（实测「快刀青衣」2014 年
几篇用了 `img.huxiu.com`），表现为「引用了但磁盘上有 5 个缺失」但 `fix_remote.py` 报 0 篇
（因为引用已被改写成本地路径）。用下面这个补：
```bash
<python> scripts/fix_ext_img.py --dir "<vault>/Resources/<公众号名>"
# 外站有防盗链时加 --referer "https://www.huxiu.com/" 之类
```

**判断「某篇无图是否正常」的正确口径**：不能只数页面里的 mmbiz URL —— 封面 `og:image`、头像 `round_head_img`、`msg_cdn_url`、UI 元素都会混进来（曾出现「每篇恰好 4 张」的假象）。要看 `<img ... data-src=` 的出现次数，以及 `picture_page_info_list` 是否非空。

**又一个假象（2026-10-05 踩到）**：数 `<img[^>]+data-src=` 时，页面 JS 里生成微信表情的模板
`<img src="..." class="we-emoji" ... data-src="` 会被命中 → **每篇都被数成恰好 1 张**。
过滤掉 `we-emoji`，或只在 `#js_content` 内部统计。详见 `references/pitfalls.md` 第三节。

**抓到「未命名-N」空壳时的判别**（三种响应要分清）：

| 响应 | 字节数 | 含义 | 能救吗 |
|---|---|---|---|
| 含「参数错误」 | ~31.7KB | 链接残缺（内存扫描把 `sn` 切坏了） | 修 sn 有可能，见下 |
| 只有「视频/小程序/赞/在看」 | ~17.7KB | 文章已删除或不可访问 | **救不回来**，别浪费时间 |
| 含「环境异常」 | ~31KB | UA 不对 / 被风控 | 换 PC 微信 UA、降频 |

内存扫描的 `sn` 会被粘连字符串污染（尾部多出 `Zs`、`yMh%2F` 之类垃圾）或截断到只剩十几位。
实测：把尾巴清掉能让链接从「参数错误」变成通过校验，但如果文章本身已删，照样是 17.7KB 空壳。
补位暴力破解不划算（缺 2 位要试 256 次，实测全失败）。哥飞那批 762 条里有 4 条这种残缺链接。

### Step 5 · 清理孤儿附件（需用户确认）

```bash
<python> scripts/orphan_check.py --vault "<库根>" --dir "<笔记目录>" --out orphan_list.json
<python> scripts/cleanup.py --dir "<笔记目录>" --orphan-list orphan_list.json --list-out deleted_files.txt
# 用户确认后：
<python> scripts/cleanup.py --dir "<笔记目录>" --orphan-list orphan_list.json --yes
```
⚠️ 本机 `SHFileOperationW` 会「返回码非 0 但文件确实删掉了」，**执行后必须用 ls/计数复核**，别信脚本返回的成功批次数。

## 增量更新（第二次及以后）

`wechat_sync.py` 全包了，判重靠**现有笔记 frontmatter 里的 `source:`**（取 `mid` + `idx`），
不依赖任何外部状态文件——笔记在，就知道抓过哪些；笔记被删了，下次自然补回来。

```bash
<python> scripts/wechat_sync.py --account "橘AI"                       # 增量（biz/vault 从注册表读）
<python> scripts/wechat_sync.py --account "橘AI" --dry-run             # 只看看有几篇新的，不抓
<python> scripts/wechat_sync.py --account "橘AI" --mode full           # 无视已有笔记重抓全部
<python> scripts/wechat_sync.py --all                                  # 所有已收录的号轮流更新
<python> scripts/wechat_sync.py --list                                 # 列出收录情况
```

- 注册表：`~/.workbuddy/state/gongzhonghao-zhuaqu/accounts.json`（biz / vault / 篇数 / 上次同步）
- 中间产物：`~/.workbuddy/state/gongzhonghao-zhuaqu/work/<号名>/`（mem_links.json、urls_*.txt、report.json）
- 新链接 ≤60 条走全量归属校验，>60 条只抽样 30 条（抓取阶段还有 `--biz` 兜底）
- 收尾用 `reindex.py` 全量重建索引，**不走** `finalize.py`，避免每次更新都触发去重删除

⚠️ 增量能抓到多少，取决于微信内存里还留着这个号的多少链接。用户如果很久没打开这个号，
可能只捞到最近几篇——这是内存扫描路线的固有限制，不是 bug。要补历史就在微信里
打开该号主页滚到底再跑一次（会 diff 出旧文章）。

## MCP 用法

`mcp/server.py` 已把上面这些包成 MCP 工具，注册名 `gongzhonghao-zhuaqu`
（写在 `~/.workbuddy/mcp.json`，用受管 venv 的 python 跑）。
装 MCP SDK：`pip install mcp -i https://pypi.org/simple`
（注意装出来是 2.x，`FastMCP` 已改名 `MCPServer`，代码里两种 import 都兼容）。

| 工具 | 作用 |
|---|---|
| `wechat_list_accounts` | 列出已收录的号（biz / 篇数 / 上次同步） |
| `wechat_preview_new(account, biz?, vault?)` | 只数有几篇新文章，不抓 |
| `wechat_update_account(account, biz?, vault?, mode?, workers?)` | 抓新文章 + 更新索引 |
| `wechat_update_all(mode?, workers?)` | 所有号轮流更新 |
| `wechat_find_biz(name)` | 从微信内存里定位某个号的 biz |

新增号后在连接器管理页点「信任」才会生效。日志在
`~/.workbuddy/state/gongzhonghao-zhuaqu/mcp.log`。

## 脚本清单

| 脚本 | 作用 |
|---|---|
| **`wechat_sync.py`** | **一键全流程 + 增量**（日常入口） |
| `reindex.py` | 非破坏性重建 `00-索引.md`（增量收尾用） |
| `mem_scan_links.py` | 扫微信进程内存拿文章链接（多进程并发，纯 ctypes） |
| `mem_ctx_scan.py` | 在内存里搜公众号名，靠上下文定位 biz（不知道 biz 时首选） |
| `extract_chksm_links.py` | 直接从内存提「带 chksm 的干净完整链接」（clean_links 大批空壳时的替代方案） |
| `clean_links.py` | 粘连字符串切分、按 biz 过滤、保留 chksm、按 mid+idx 去重择优 |
| `filter_by_biz.py` | 流式读前 120KB 判定归属（并发 8 + 可选抽样），产出最终 URL 清单 |
| `wechat_to_obsidian.py` | **核心**：抓正文 → Markdown → 图片本地化 → 写笔记 + 索引 |
| `finalize.py` | 去重、改回 ` (2)` 文件名、重建索引 |
| `check_images.py` | 图片引用体检 |
| `fix_remote.py` | 补救正文里残留的远程图片（只处理微信图床） |
| `fix_ext_img.py` | 把非微信图床的外链图（如 huxiu 图床）下载并本地化 |
| `orphan_check.py` | 全库确认孤儿附件无人引用，按 md5 分类 |
| `cleanup.py` | 送回收站清理（先出清单，加 `--yes` 才执行） |
| `mcp/server.py` | MCP 服务（5 个工具），让 Agent 一句话触发抓取 |

## 深入阅读

- `references/pitfalls.md` —— 本次实战踩出来的全部坑与修复代码（图片丢失四层根因、JS 模板、图集型文章等）

## 已验证战绩

| 公众号 | 扫描条数 | 筛出候选 | 最终入库 | 图片 | 体检结果 |
|---|---|---|---|---|---|
| 某科技博主号 | 2577 | 199 | **197 篇** | 910 张本地化 | attachments 728 个 ↔ 正文引用 728 个，零缺失零孤儿 |
| 比高（私域/流量方向） | 3673 | 197 | **187 篇** | 574 张本地化 | attachments 485 个 ↔ 正文引用 485 个，零缺失零孤儿（另有 10 条链接已失效，抓出来是「参数错误」空壳，已由 Step 3 丢弃） |
| 哥飞（出海/SEO/独立开发） | 5518 | 762 | **741 篇** | 11746 张本地化 | attachments 10948 个 ↔ 正文引用 10938 个，零缺失、裸 img 0；孤儿 10 个（未清理）；38 篇无图笔记经抽查确认原文真无图 |
| 花叔（AlchainHust / 小猫补光灯作者 / AI Native Coder） | 3095 | 386 | **386 篇** | 4991 张下载、0 失败 | attachments 4980 ↔ 引用 4980，零缺失零孤儿、裸 img 0；41 篇无图笔记抽 8 篇回查确认原文纯文字。跨度 2018-12~2026-10，约 198 万字，attachments 目录 2.4G（GIF 多，留意磁盘） |
| 快刀青衣（得到联合创始人 / AI 应用） | 2431 | 298 | **298 篇** | 1876 张本地化 | attachments 1876 ↔ 引用 1876，零缺失零孤儿、裸 img 0；34 篇无图笔记抽查确认原文真无图。跨度 2014-01~2026-10，约 127.9 万字 |
| 橘AI（Orange AI，原名「橘子汽水铺」） | 3999 | 546 | **546 篇**（后又增量 +2 = 548） | 3135 张本地化、0 失败 | attachments 3135 ↔ 引用 3135，零缺失零孤儿、裸 img 0；264 篇无图笔记抽 8 篇回查（`js_content` 与 `content_noencode` 两种壳都查）确认原文纯文字。跨度 2024-05~2026-10，约 74.6 万字，attachments 1.25G |
| 夙愿的AI实验室（夙愿学长 / AI 工具测评） | 1492 | 202 | **197 篇** | 3725 张下载、0 失败 | attachments 3192 ↔ 引用 3192，零缺失零孤儿、裸 img 0；26 篇无图笔记抽 8 篇回查确认原文真无图。跨度 2024-02~2026-09，约 66.7 万字，attachments 1.05G。5 条 sn 残缺链接为「参数错误」空壳已删；同名重发（mid 2247484852 / 2247484855）两篇都保留、加日期后缀区分 |

> **提速与增量改造（2026-10-21）**：橘AI 这 546 篇是改造前的最后一次全量跑（约 40 分钟）：
> 内存扫描 2m48s + 归属校验 10m09s + 抓取 26m。改造后同样的量预计 **8~10 分钟**——
> 扫描改多进程 45s、校验并发 8 + 抽样、抓取并发 6（实测 60 篇 49s，改造前 546 篇 26m）。
> 之后每次增量：扫描 45s + 抓几篇，约 1 分钟。

> 快刀青衣这批是 chksm 坑的发现场景：`clean_links.py` 旧版丢掉 chksm 后 298 条
> **全部**返回 17.7KB 空壳，一度误判成「文章全被删」；靠 `mem_ctx_scan.py` 定 biz、
> `extract_chksm_links.py` 重提链接才跑通。相关修复已并入 `clean_links.py`。

> 第二行的 63 篇「无图笔记」经回原文抽查（`data-src` 计数与 `picture_page_info_list` 均为空），
> 确认原文就是纯文字，不是抓取漏图 —— 详见 Step 4 的判定口径。
