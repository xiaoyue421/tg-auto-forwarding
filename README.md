# TelegramForwarder

一个带中文 Web 控制台的 Telegram 转发工具。

它支持多规则实时监听、多源到多目标转发、账号 / Bot 多种发送策略、历史消息搜索、失败队列和转发监控，适合需要稳定筛选与分发 Telegram 消息的场景。

## 功能亮点

- 中文 Web 控制台，适合直接在浏览器里管理配置
- 多规则同时运行
- 多源到多目标、多对一转发
- 支持账号目标和 Bot 目标同时配置
- 支持规则级发送策略
- 支持关键词、正则、黑名单、资源预设过滤
- 支持“需要媒体”和“需要文本内容”组合判断
- 支持历史消息搜索后手动指定转发
- 支持本地发送队列、失败重试、成功历史去重
- 支持 Docker 部署

## 适用场景

- 监听频道 / 群里的消息并按规则转发
- 将不同来源的资源消息集中整理到目标群
- 按关键词、正则或资源类型自动筛选
- 需要后台查看实时转发日志和失败记录
- 需要用用户账号监听，但按 Bot 或账号策略发送

## 工作流程

系统大致按下面顺序工作：

1. 用户账号监听源频道 / 群的新消息
2. 按规则做内容匹配和过滤
3. 命中的消息进入本地发送队列
4. dispatcher 按发送策略把消息发往账号目标 / Bot 目标
5. 后台展示转发日志、失败任务、成功历史和 worker 状态

## 环境要求

- Python `>= 3.11`
- 一个有效的 Telegram `API ID` 和 `API HASH`
- 一个已登录的用户账号 `session_string`
- 如果要用 Bot 发送，还需要一个或多个 Bot Token

建议：

- 监听建议优先使用用户账号
- Bot 更适合做“发送端”，不适合作为通用监听端

## 快速开始

### 1. 安装依赖

```powershell
pip install -r requirements.txt
```

### 2. 准备 `.env`

最少可以先准备这些字段：

```dotenv
TG_DASHBOARD_PASSWORD=admin
TG_API_ID=你的_api_id
TG_API_HASH=你的_api_hash
TG_SESSION_STRING=
TG_BOT_TOKEN=
TG_FORWARD_STRATEGY=parallel
TG_SOURCE_CHAT=@source_channel
TG_TARGET_CHATS=@target_channel_1,@target_channel_2
TG_BOT_TARGET_CHATS=
```

也可以直接从示例文件开始：

```powershell
Copy-Item .env.example .env
```

说明：

- `TG_SESSION_STRING` 是用户账号登录后的会话
- `TG_BOT_TOKEN` 可以留空；如果有多个，使用英文逗号分隔
- `TG_SOURCE_CHAT`、`TG_TARGET_CHATS` 主要用于单规则简化模式
- 使用 Web 控制台多规则管理时，系统会把规则写入 `TG_RULES_JSON`

### 3. 生成 Telegram 登录会话

```powershell
python -m tg_forwarder login --config .env --save-env
```

### 4. 启动 Web 控制台

```powershell
python -m tg_forwarder web --config .env --host 0.0.0.0 --port 8080
```

浏览器打开：

```text
http://127.0.0.1:8080
```

默认控制台密码：

```text
admin
```

## Docker 部署

### 1. 构建并启动

```powershell
docker compose up -d --build
```

### 2. 打开控制台

```text
http://127.0.0.1:8080
```

### 3. 在容器里生成 `session_string`

```powershell
docker compose run --rm tg-forwarder python -m tg_forwarder login --config /workspace/.env --save-env
```

### 4. 当前 Docker 说明

当前仓库里的 `docker-compose.yaml` 已经做了这些事：

- 将项目目录挂载到容器内 `/workspace`
- 将队列数据库持久化到 Docker volume `/data`
- 默认从 `/workspace/.env` 读取配置
- 前后端源码改动后，通常只需要重启容器，不需要每次重新打包镜像

## Web 控制台说明

### 基础配置

可以设置：

- `API ID`
- `API HASH`
- `SESSION STRING`
- `BOT TOKEN`
- 全局发送策略
- 限流保护
- 启动通知
- 代理

### 转发规则

每条规则都可以独立设置：

- 规则名称
- 是否启用
- 源频道 / 群
- 账号目标频道 / 群
- Bot 目标频道 / 群
- 规则级发送策略
- 是否监听编辑消息
- 是否转发自己发出的消息
- 命中任一关键词
- 必须全部命中
- 黑名单关键词
- 正则任一命中
- 正则全部命中
- 正则黑名单
- 资源预设
- 是否需要媒体
- 是否需要文本
- 内容匹配模式
- 大小写敏感

### 历史搜索

支持从所有已配置源频道中做模糊搜索。

当前搜索范围只包含 Telegram 原消息本身：

- 正文
- caption
- 按钮文字
- 消息里直接带的链接文本

搜索结果支持：

- 按频道切换查看
- 按规则默认目标直接转发
- 打开原消息

### 状态、队列和日志

后台可以查看：

- 最近日志
- 转发监控日志
- 当前 worker 状态
- dispatcher 状态
- 失败任务队列
- 成功历史统计

## 发送策略

全局和单条规则都可以设置发送策略：

- `parallel`
  账号目标和 Bot 目标同时尝试发送
- `account_first`
  先发账号目标，全部失败后再回退到 Bot
- `bot_first`
  先发 Bot 目标，全部失败后再回退到账号
- `account_only`
  只用用户账号发送
- `bot_only`
  只用 Bot 发送

如果配置了多个 Bot Token，系统会按顺序尝试：

1. `bot#1`
2. `bot#2`
3. `bot#3`
4. 全部失败后，再按当前策略决定是否回退到用户账号

## 规则匹配逻辑

### 黑名单关键词 / 黑名单正则

优先级最高。

只要命中黑名单，就直接跳过转发。

### 必须全部命中

这一组里的所有条件都要满足。

示例：

```text
必须全部命中：
- 115cdn
- 更新
```

那么消息里必须同时包含这两个条件。

### 命中任一关键词 / 任一正则 / 资源预设

这一组只要命中一个即可。

示例：

```text
命中任一关键词：
- ed2k
- magnet
- 115cdn
```

消息里只要出现其中一个，就算通过这组条件。

### 媒体和文本组合

可以同时勾选：

- `需要媒体`
- `需要文本内容`

再通过 `内容匹配模式` 控制：

- `all`
  两个条件都要满足
- `any`
  满足其中一个即可

### 最终判断顺序

可以简单理解为：

1. 先检查黑名单
2. 再检查媒体 / 文本条件
3. 再检查“必须全部命中”
4. 最后检查“任一命中”

## 如何测试

### 测试实时自动转发

1. 使用账号 A 生成 `session_string`
2. 让账号 A 加入源群和目标群
3. 如果要测试 Bot 转发，把 Bot 拉进目标群并给它发言权限
4. 在后台新增一条规则
5. 保存配置
6. 校验配置
7. 启动后端
8. 使用另一个账号 B 往源群发消息
9. 查看目标群是否收到

注意：

- 默认不会转发当前登录账号自己发出的消息
- 测试时建议用另一个账号发消息
- 如果源和目标配成同一个地方，要注意避免循环转发

### 测试历史搜索和指定转发

1. 打开“搜索”
2. 输入关键词
3. 点击搜索
4. 找到对应消息
5. 点击“按已配置目标转发”

## 常见问题

### 提示 `simple mode requires TG_SOURCE_CHAT`

说明你还在使用单规则简化模式，但没有填源频道。

解决方式：

- 如果你使用 Web 控制台，直接去“规则设置”里新增规则并保存
- 如果你坚持用单规则模式，就在 `.env` 里补全 `TG_SOURCE_CHAT`

### 规则修改后要不要重启

建议修改后点一次“重启后端”，最稳妥。

### Bot 为什么收不到另一个 Bot 发的消息

这是 Telegram 本身的权限限制，不是这个项目的单独问题。

如果你的核心需求是“稳定监听”，建议：

- 监听侧使用用户账号
- 发送侧再按策略选择账号或 Bot

### 重启后为什么还会继续发送队列里的任务

这是设计使然。

发送队列是为了避免消息命中后因为容器重启、网络波动或短暂失败而直接丢失。已经成功完成的消息会从队列中移除；未完成的任务会保留，等服务恢复后继续处理。

### 为什么 Bot 启动通知会报连接错误

如果报错发生在 `startup_notifier`，通常表示：

- Bot Token 无效
- 当前网络连不上 Telegram
- 代理配置不可用
- 容器网络与宿主机网络环境不同

这种错误一般只影响“启动通知”，不一定代表主转发逻辑已经完全不可用。

## 项目结构

```text
src/tg_forwarder/
  cli.py                 命令行入口
  webapp.py              FastAPI Web 控制台
  supervisor.py          worker 进程管理
  worker.py              实时监听和命中判断
  dispatcher.py          发送队列调度
  dispatch_queue.py      队列与历史记录
  forwarder.py           账号 / Bot 发送逻辑
  filters.py             规则匹配逻辑
  dashboard_actions.py   搜索和手动转发
  startup_notifier.py    启动通知
  telegram_clients.py    Telegram 客户端与代理连接
  web/static/            前端静态页面
```

## 开源发布前建议

发布到 GitHub 前，建议再确认这几项：

- 不要提交 `.env`
- 不要提交 `session`、`session-journal`
- 不要提交 `*.sqlite3`、`*.db`
- 不要提交运行日志和临时压缩包
- 把 `TG_DASHBOARD_PASSWORD` 改成自己的密码
- 如果要公开演示，请先清理真实频道、群组和 Bot 信息

当前仓库已经忽略这些常见本地文件：

- `.env`
- `__pycache__`
- `*.pyc`
- `*.sqlite3`
- `*.db`
- `*.zip`
- IDE 配置目录

## 注意事项

- 请遵守 Telegram 的服务条款、频道规则和当地法律法规
- 请不要将本项目用于未授权的数据抓取、骚扰或违规分发
- 用户账号和 Bot Token 都属于敏感信息，请妥善保管

## License

本项目使用 `MIT` License。

完整许可证内容见：

- [LICENSE](/e:/dm/TelegramForwarder-main/LICENSE)
