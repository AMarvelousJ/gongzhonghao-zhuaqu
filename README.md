# 公众号文章抓取 → Obsidian

把一个微信公众号的**历史文章全量**抓下来，生成 Obsidian 笔记：每篇一个 `.md`，带 YAML frontmatter，
**图片全部下载到本地**并用相对路径引用，最后生成一份按日期倒序的 `00-索引.md`。

不依赖 wechatDownload、WeChatMsg 之类第三方下载器。2026 年起微信已封禁跨号文章列表接口，
本项目走的是「**微信进程内存扫描** + 公开文章页抓取」路线 —— 两套门槛方案都在 `SKILL.md` 里。

实测战绩：单号 197 篇、910 张图片；另一号 187 篇、574 张图片，
最终 attachments 文件数与正文引用数**一一对应，零缺失零孤儿**。

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
git clone https://github.com/<你的账号>/gongzhonghao-zhuaqu.git \
    ~/.workbuddy/skills/gongzhonghao-zhuaqu
```

也可以纯命令行用（SKILL.md 里的每一步都是可以直接敲的命令，把 `<python>` 换成你的解释器路径）。

## 快速开始

```bash
# 0. 先装依赖
pip install -r requirements.txt

# 1. 在 PC 微信里打开目标公众号主页 → 历史消息滚到最底，让全部文章渲染出来
#    （在系统浏览器里点文章顶部的号名会强制跳回微信，那条路走不通）

# 2. 扫描微信进程内存，捞出文章链接（约 2 分钟，务必后台跑）
python -u scripts/mem_scan_links.py mem_links.json

# 3. 清洗去重，得到候选链接（--biz 的取法见 SKILL.md 的 Step 0）
python scripts/clean_links.py mem_links.json urls_clean.txt --biz "<biz字符串>"

# 4. 逐个判定归属，产出最终清单
python scripts/filter_by_biz.py "$PWD/urls_clean.txt" \
    --biz "<biz字符串>" --name "<公众号名>" \
    --out "$PWD/urls_final.txt" --report "$PWD/candidates_report.json"

# 5. 抓正文并生成笔记（图片落本地）
python -u scripts/wechat_to_obsidian.py \
    --urls urls_final.txt \
    --vault "<你的 Obsidian 库根目录>" \
    --account "<公众号名>" \
    --images local --delay 0.8

# 6. 收尾：去重 + 重建索引（先预览，确认无误再加 --apply）
python scripts/finalize.py --dir "<库根目录>/Resources/<公众号名>"      # 预览
python scripts/finalize.py --dir "<库根目录>/Resources/<公众号名>" --apply

# 7. 图片体检（必须做）
python scripts/check_images.py --dir "<库根目录>/Resources/<公众号名>"
```

产物结构：

```
<vault>/Resources/<公众号名>/
├── 00-索引.md          按日期倒序的双链索引
├── <标题>.md           每篇一个笔记
└── attachments/        本地化的图片
```

体检合格标准：**「引用了但磁盘上没有」= 0**，且「正文含裸 `<img>` 标签」= 0。

## 脚本清单

| 脚本 | 作用 | 平台 |
|---|---|---|
| `mem_scan_links.py` | 扫微信进程内存拿文章链接（纯 ctypes，无第三方依赖） | Windows |
| `clean_links.py` | 粘连字符串切分、按 biz 过滤、按 mid+idx 去重择优 | 通用 |
| `filter_by_biz.py` | 流式读前 40KB 判定归属，产出最终 URL 清单 | 通用 |
| `wechat_to_obsidian.py` | **核心**：抓正文 → Markdown → 图片本地化 → 写笔记 + 索引 | 通用 |
| `finalize.py` | 去重、改回 ` (2)` 文件名、重建索引 | 通用 |
| `check_images.py` | 图片引用体检 | 通用 |
| `fix_remote.py` | 补救正文里残留的远程图片 | 通用 |
| `orphan_check.py` | 全库确认孤儿附件无人引用，按 md5 分类 | 通用 |
| `cleanup.py` | 送回收站清理（先出清单，加 `--yes` 才执行） | Windows |

## FAQ

**不知道 biz 怎么办？**
统计 `mem_links.json` 里各 biz 的「唯一 mid+idx 数」，取最大的那个候选，抽样抓几篇看页面里的
`var nickname = htmlDecode("...")`，就能确认归属 —— 不必到处找链接。

**抓出来一堆空白笔记？**
几乎一定是 User-Agent 不对。必须用 PC 微信 UA，普通 Chrome UA 会被风控返回「环境异常」壳页。
另外 `apparent_encoding` 会把部分页面误判成 GBK 导致标题乱码，务必写死 `r.encoding = "utf-8"`。
详见 `references/pitfalls.md`。

**图片丢了？**
四种成因会叠加：懒加载（`data-src` 没有 `src`）、HTML 实体不同形、`src` 与 `data-src` 是两个不同 URL、
图集型文章没有 `#js_content`。`references/pitfalls.md` 第三节有完整修法。

**怎么看某篇是不是本来就没图？**
不能只数页面里的 mmbiz URL —— 封面、头像、UI 元素都会混进来（典型假象：每篇恰好 4 张）。
要看 `<img ... data-src=` 的出现次数，以及 `picture_page_info_list` 是否非空。

## 深入阅读

- `SKILL.md` —— 完整工作流、硬性约束、参数说明
- `references/pitfalls.md` —— 实战踩出来的全部坑（图片丢失四层根因、JS 模板正文、图集型文章等）

## 免责声明

本项目仅供**个人学习与技术备份**使用。微信公众号内容版权归原作者所有，抓取前请确保你有合法使用权，
不要二次传播、分发或用于商业用途，并请遵守微信公众平台相关服务条款。
因使用本项目造成的任何后果由使用者自行承担。

使用时请自行控制请求频率（默认 `--delay 0.8` 秒），避免对服务造成压力。

## License

[MIT](LICENSE) —— 记得把 `LICENSE` 里的 `Your Name` 换成你自己的名字。
