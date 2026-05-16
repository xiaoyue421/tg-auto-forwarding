# Telegraph（telegra.ph）独立规则示例

与 **HDHive 直链转发无关**。启用后由 `tgph/` 拉取 [Telegraph 文章](https://telegra.ph/) 的 **HTML**，在页面正文与文内链接上应用本规则的关键词 / 正则，再提取直链（如 `115cdn.com`）转发。

## 示例文章

<https://telegra.ph/星辰光辉更胜太阳-更新412集-1080P-amzn源-官方简体中文字幕332GB神秘G-10-24>

文内典型直链（以实际页面为准）：`https://115cdn.com/s/...?password=...`

## 控制台规则（推荐）

| 项 | 建议值 |
|----|--------|
| **Telegraph：解析 telegra.ph 文章 HTML 并转发文内直链** | ✅ 勾选 |
| **仅当 Telegraph 页面 HTML 命中下方关键词/正则时才转发** | ✅ 勾选（推荐） |
| **命中任一关键词** | `115cdn` 或 `4K` 或 `REMUX`（写在页面里的字样） |
| **必须全部命中** | 按需，如 `115cdn` + `4K` |
| **黑名单关键词** | 按需 |

说明：

- 消息里须带有 `telegra.ph/…` 链接；**不会**去读 HDHive 站点设置。
- **未勾选**「仅当页面 HTML 命中…」：只要文章 HTML 里能解析出直链就转发。
- **勾选**「仅当页面 HTML 命中…」：用下方关键词 / 正则对 **文章 HTML**（含文内 `href` 链接）匹配，须至少填一类「命中任一 / 必须全部 / 正则」。
- 与「HDHive：识别 resource…」可同时开启，二者互不依赖。
- 拉取 `telegra.ph` 会使用 **基础配置** 中的 `TG_PROXY_*`（与 Telegram 相同）；容器内若报 `Network is unreachable`，请填写可访问外网的代理。

## 命令行自测（对页面 HTML 匹配）

```powershell
$env:PYTHONPATH = "src;."
python -m tgph.cli --require-match --keyword 115cdn --pretty "https://telegra.ph/你的文章路径"
```

不启用页面匹配时（等同未勾选「仅当页面 HTML 命中」）：

```powershell
python -m tgph.cli --pretty "https://telegra.ph/你的文章路径"
```
