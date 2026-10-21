# 公众号文章抓取 → Obsidian

把一个微信公众号的**历史文章全量**抓下来，生成 Obsidian 笔记：每篇一个 `.md`，带 YAML frontmatter，
**图片全部下载到本地**并用相对路径引用，最后生成一份按日期倒序的 `00-索引.md`。

不依赖 wechatDownload、WeChatMsg 之类第三方下载器。2026 年起微信已封禁跨号文章列表接口，
本项目走的是「**微信进程内存扫描** + 公开文章页抓取」路线 —— 门槛与坑都写在 `SKILL.md` 里。

实测战绩：单号最多 **741 篇 / 11746 张图片**，已跑通 7 个号共 2900+ 篇，
最终 attachments 文件数与正文引用数**一一对应，零缺失零孤儿**（详见下方战绩表）。

## 适用环境

| 项目 | 要求 |
|---|---|
| 操作系统 | **Windows 才能跑全流程**。`mem_scan_links.py` 和 `cleanup.py` 依赖 Windows API；其余脚本跨平台 |
| Python | 3.9+ |
| 依赖 | `pip install -r requirements.txt` |
| 网络 | 能访问 `mp.weixin.qq.com` |

## 安装

作为 WorkBuddy / Claude Code 的 Skill 使用：

```bash
git clone https://github.com/AMarvelousJ/gongzhonghao-zhuaqu.git \
    ~/.workbuddy/skills/gongzhonghao-zhuaqu
```

也可以纯命令行用（`SKILL.md` 里的每一步都是可以直接敲的命令，把 `<python>` 换成你的解释器路径）。

## 快速开始（推荐：一条命令）

日常就走这条。`wechat_sync.py` 一条命令跑完「内存扫描 → 清洗 → diff 出新文章 → 并发抓取 → 重建索引」：

```bash
# 首次：带上 biz 和库路径，之后它们会记进注册表
python -u scripts/wechat_sync.py --account "<公众号名>" --biz "<biz字符串>" \
    --vault "<你的 Obsidian 库根目录>"

# 之后每次增量同步，不带任何参数也行
python -u scripts/wechat_sync.py --account "<公众号名>"
python -u scripts/wechat_sync.py --account "<公众号名>" --dry-run   # 只看看有几篇新的
python -u scripts/wechat_sync.py --all                             # 注册表里所有号轮流更新
python -u scripts/wechat_sync.py --list                            # 看看收录了哪些号
```

前提：在 **PC 微信里**打开该号主页 → 历史消息**滚到最底**，让全部文章渲染出来
（在系统浏览器里点文章顶部的号名会强制跳回微信，那条路走不通）。

判重靠现有笔记 frontmatter 里的 `source:`（取 `mid` + `idx`），不依赖外部状态文件 ——
笔记在就知道抓过哪些，笔记删了下次自然补回来。

> 增量能抓到多少，取决于微信内存里还留着这个号的多少链接。很久没打开这个号，
> 可能只捞到最近几篇 —— 这是内存扫描路线的固有限制。要补历史就在微信里打开主页滚到底再跑一次。

## 分步流程（想自己控制每一步时）

```bash
# 1. 扫描微信进程内存（多进程并发，实测 45 秒；务必后台跑）
python -u scripts/mem_scan_links.py mem_links.json --workers 4 --biz "<biz字符串>"

# 2. 清洗去重（保留 chksm、按 mid+idx 去重择优）
python scripts/clean_links.py mem_links.json urls_clean.txt --biz "<biz字符串>"

# 3. 归属校验（并发 8，可选 --sample 30 抽样）
python scripts/filter_by_biz.py "$PWD/urls_clean.txt" \
    --biz "<biz字符串>" --name "<公众号名>" \
    --out "$PWD/urls_final.txt" --report "$PWD/candidates_report.json" --workers 8

# 4. 抓正文并生成笔记（并发 6，图片落本地）
python -u scripts/wechat_to_obsidian.py \
    --urls urls_final.txt \
    --vault "<你的 Obsidian 库根目录>" \
    --account "<公众号名>" \
    --images local --workers 6

# 5. 重建索引（非破坏性，增量收尾用它；日常不必跑 finalize）
python scripts/reindex.py --dir "<库根目录>/Resources/<公众号名>" --account "<公众号名>"

# 6. 图片体检（必须做）
python scripts/check_images.py --dir "<库根目录>/Resources/<公众号名>"
```

不知道 biz？跑 `python -u scripts/mem_ctx_scan.py --name "<公众号名>"`，
它直接在微信进程内存里搜号名，把命中位置附近的 `__biz` 统计出来，出现最多的那个就是。

产物结构：

```
<vault>/Resources/<公众号名>/
├── 00-索引.md          按日期倒序的双链索引
├── <标题>.md           每篇一个笔记
└── attachments/        本地化的图片
```

体检合格标准：**「引用了但磁盘上没有」= 0**，且「正文含裸 `<img>` 标签」= 0。

## 性能

改造前串行版跑 546 篇约 40 分钟。现在：

| 阶段 | 改造前 | 现在 |
|---|---|---|
| 内存扫描 | 2m48s | **45s**（多进程，瓶颈是 GIL 密集的正则，线程池没用） |
| 归属校验 | 546 条约 10m | **1 分钟内**（并发 8 + 可选抽样） |
| 抓取 | 546 条约 26m | **约 7 分钟**（并发 6；实测 60 篇 / 399 张图 49s） |

并发再往上加边际收益很小（`--workers 10` 只快 5s），反而更招风控。默认 6。

## 战绩

| 公众号 | 扫描条数 | 候选 | 入库 | 图片 | 体检 |
|---|---|---|---|---|---|
| 哥飞（出海 / SEO / 独立开发） | 5518 | 762 | **741 篇** | 11746 张 | 零缺失、裸 img 0 |
| 橘AI（Orange AI） | 3999 | 546 | **548 篇** | 3135 张 | 零缺失零孤儿、裸 img 0 |
| 花叔（AI Native Coder） | 3095 | 386 | **386 篇** | 4991 张、0 失败 | 零缺失零孤儿、裸 img 0 |
| 快刀青衣（得到联合创始人） | 2431 | 298 | **298 篇** | 1876 张 | 零缺失零孤儿、裸 img 0 |
| 夙愿的AI实验室 | 1492 | 202 | **197 篇** | 3725 张、0 失败 | 零缺失零孤儿、裸 img 0 |
| 某科技博主号 | 2577 | 199 | **197 篇** | 910 张 | 零缺失零孤儿 |
| 比高（私域 / 流量） | 3673 | 197 | **187 篇** | 574 张 | 零缺失零孤儿 |

每一号的无图笔记都做过回原文抽查，确认原文本来就无图，不是漏抓。

## 脚本清单

| 脚本 | 作用 | 平台 |
|---|---|---|
| **`wechat_sync.py`** | **一键全流程 + 增量**（日常入口） | Windows |
| `reindex.py` | 非破坏性重建 `00-索引.md`（增量收尾用） | 通用 |
| `mem_scan_links.py` | 扫微信进程内存拿文章链接（多进程并发，纯 ctypes） | Windows |
| `mem_ctx_scan.py` | 在内存里搜公众号名、靠上下文定位 biz | Windows |
| `clean_links.py` | 粘连字符串切分、按 biz 过滤、保留 chksm、按 mid+idx 去重择优 | 通用 |
| `filter_by_biz.py` | 流式读前 120KB 判定归属（并发 8 + 可选抽样） | 通用 |
| `wechat_to_obsidian.py` | **核心**：抓正文 → Markdown → 图片本地化 → 写笔记 + 索引 | 通用 |
| `extract_chksm_links.py` | 直接从内存提带 chksm 的完整链接（大批空壳时的替代方案） | Windows |
| `finalize.py` | 去重、改回 ` (2)` 文件名、重建索引（**会删东西**，慎用） | 通用 |
| `check_images.py` | 图片引用体检 | 通用 |
| `fix_remote.py` | 补救正文里残留的远程图片（微信图床） | 通用 |
| `fix_ext_img.py` | 非微信图床的外链图（如 huxiu）下载并本地化 | 通用 |
| `orphan_check.py` | 全库确认孤儿附件无人引用，按 md5 分类 | 通用 |
| `cleanup.py` | 送回收站清理（先出清单，加 `--yes` 才执行） | Windows |
| `mcp/server.py` | MCP 服务（5 个工具），Agent 一句话触发抓取 | Windows |

## MCP 用法

配好之后，在 Agent 里直接说「更新一下橘AI的公众号文章」即可，不用记命令：

```bash
pip install mcp -i https://pypi.org/simple
```

`mcp/server.py` 提供 5 个工具：`wechat_list_accounts`、`wechat_preview_new`、
`wechat_update_account`、`wechat_update_all`、`wechat_find_biz`。
注册到 `~/.workbuddy/mcp.json`（注册名 `gongzhonghao-zhuaqu`），新增后在连接器管理页点「信任」生效。

## FAQ

**不知道 biz 怎么办？**
首选 `mem_ctx_scan.py` —— 它在微信进程内存里搜号名（同时试 UTF-8 / UTF-16LE），把命中位置 ±3KB
内出现的 `__biz=` 全统计出来，出现最多的那个基本就是。比「统计 biz 分布」准得多
（分布法容易被"用户还开着别的号"干扰）。

**扫出来只有几条链接？**
只匹配 `https://mp.weixin.qq.com/s/xxx` 短链只能捞到 8 条（漏 95%）。必须匹配
`mp.weixin.qq.com/s\?__biz=...&mid=...&idx=...&sn=...` 分享链。另外内存里的字符串是粘连的
（`URL1\x00\x00URL2\x00...`），要按合法 URL 字符集正则切开，同一篇取 `sn` 最长的。

**抓出来一堆空白笔记？**
几乎一定是 User-Agent 不对。必须用 PC 微信 UA，普通 Chrome UA 会被风控返回「环境异常」壳页。
另外 `apparent_encoding` 会把部分页面误判成 GBK 导致标题乱码，务必写死 `r.encoding = "utf-8"`。
还有一种：链接里的 `chksm` 参数丢了会让**整批**返回 17.7KB 空壳，看着跟文章被删一模一样。
详见 `references/pitfalls.md`。

**图片丢了？**
四种成因会叠加：懒加载（`data-src` 没有 `src`）、HTML 实体不同形、`src` 与 `data-src` 是两个不同 URL、
图集型文章没有 `#js_content`。还有一种：正文只存在于 `content_noencode` 这个 JS 字符串里，
其中 `<` `>` `"` 写成了 `\x3c \x3e \x22`，不解码就一屏乱码且图片全丢。
`references/pitfalls.md` 第三节有完整修法。

**怎么看某篇是不是本来就没图？**
不能只数页面里的 mmbiz URL —— 封面、头像、UI 元素都会混进来（典型假象：每篇恰好 4 张）。
要看 `#js_content` 内 `<img ... data-src=` 的出现次数（**排除 `we-emoji` 表情模板**，
否则每篇都被数成恰好 1 张），以及 `picture_page_info_list` 是否非空。

**文件名出现几百字长标题？**
微信「文字消息」没有 `var msg_title`，此时 `og:title` 会被塞满整篇正文。
本项目只在「≤80 字且不含换行」时才采信 `og:title`，否则用正文首句提炼。

## 深入阅读

- `SKILL.md` —— 完整工作流、硬性约束、参数说明、增量与 MCP 章节
- `references/pitfalls.md` —— 实战踩出来的全部坑（图片丢失四层根因、JS 模板正文、图集型文章等）

## 免责声明

本项目仅供**个人学习与技术备份**使用。微信公众号内容版权归原作者所有，抓取前请确保你有合法使用权，
不要二次传播、分发或用于商业用途，并请遵守微信公众平台相关服务条款。
因使用本项目造成的任何后果由使用者自行承担。

使用时请自行控制并发与频率（默认 `--workers 6`，不建议再往上加），避免对服务造成压力。

## License

[MIT](LICENSE)
