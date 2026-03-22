# TelegramForwarder

一个带中文 Web 控制台的 Telegram 转发工具。

它适合这类场景：

- 监听一个或多个源频道 / 群
- 按多条规则实时筛选消息
- 命中后转发到一个或多个目标频道 / 群
- 按规则选择“用户账号发送”或“Bot 发送”
- 在后台搜索历史消息后再手动指定转发

## 功能概览

- 多规则同时运行
- 多源到多目标 / 多对一转发
- 规则级关键词、正则、黑名单、资源预设过滤
- 支持“需要媒体”和“需要文本内容”组合判断
- 规则级发送策略
- 支持账号目标和 Bot 目标同时配置
- Web 控制台中文界面
- 最近日志、转发监控、失败队列、成功历史
- Docker 部署

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
4. 全部失败后，再按策略决定是否回退到用户账号

## 快速开始

### 1. 准备 `.env`

最少需要这些字段：

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

说明：

- `TG_SESSION_STRING` 是用户账号登录后的会话
- `TG_BOT_TOKEN` 可以留空，也可以填多个，英文逗号分隔
- `TG_SOURCE_CHAT`、`TG_TARGET_CHATS` 只是在单规则简化模式下使用
- 如果你使用 Web 控制台管理多规则，最终主要会写入 `TG_RULES_JSON`

### 2. 生成 Telegram 登录会话

```powershell
tg-forwarder login --config .env --save-env
```

### 3. 启动 Web 控制台

```powershell
tg-forwarder web --config .env --host 0.0.0.0 --port 8080
```

浏览器打开：

```text
http://127.0.0.1:8080
```

默认后台密码：

```text
admin
```

## Docker 启动

构建并启动：

```powershell
docker compose up -d --build
```

打开控制台：

```text
http://127.0.0.1:8080
```

如果你要在容器里生成 `session_string`：

```powershell
docker compose run --rm tg-forwarder python -m tg_forwarder login --config /workspace/.env --save-env
```

## Web 控制台可以做什么

### 1. 基础配置

可以设置：

- `API ID`
- `API HASH`
- `SESSION STRING`
- `BOT TOKEN`
- 全局发送策略
- 限流保护
- 启动通知
- 代理

### 2. 转发规则

每条规则都可以单独配置：

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

### 3. 历史搜索

支持从所有已配置源频道里做模糊搜索。

当前搜索范围只包含 Telegram 原消息本身：

- 正文
- caption
- 按钮文字
- 消息里直接带的链接文本

搜索结果可以：

- 按频道切换查看
- 直接按规则默认目标转发
- 打开原消息链接

### 4. 队列和日志

后台可查看：

- 最近日志
- 转发监控日志
- 失败任务队列
- 成功历史统计
- 当前 worker 状态
- dispatcher 状态

## 规则匹配逻辑

建议理解这三类条件的关系：

### 黑名单关键词 / 黑名单正则

优先级最高。

只要命中黑名单，就直接不转发。

### 必须全部命中

这一组里的所有条件都要满足。

例如：

```text
必须全部命中：
- 115cdn
- 更新
```

那么消息里必须同时出现这两个条件。

### 命中任一关键词 / 任一正则 / 资源预设

这一组只要命中其中一个即可。

例如：

```text
命中任一关键词：
- ed2k
- magnet
- 115cdn
```

只要消息里出现任意一个，就算通过这组条件。

### 最终判断顺序

可以简单理解为：

1. 先检查黑名单
2. 再检查内容条件
3. 再检查“必须全部命中”
4. 最后检查“任一命中”

## 如何测试

### 测试实时自动转发

1. 用账号 A 生成 `session_string`
2. 让账号 A 加入源群和目标群
3. 如果要测试 Bot 转发，把 Bot 拉进目标群并给发言权限
4. 在后台新增一条规则
5. 保存配置
6. 校验配置
7. 启动后端
8. 用另一个账号 B 往源群发消息
9. 查看目标群是否收到

注意：

- 默认不会转发当前登录账号自己发出的消息
- 测试时最好用另一个账号发消息

### 测试历史搜索和指定转发

1. 打开“消息搜索”
2. 输入关键词
3. 点击“搜索所有源”
4. 找到目标消息
5. 点击按规则目标转发，或者填写手动目标后转发

## 项目结构

```text
src/tg_forwarder/
  cli.py                 命令行入口
  webapp.py              FastAPI Web 控制台
  supervisor.py          worker 进程管理
  worker.py              实时监听和命中判断
  dispatcher.py          发送队列调度
  dispatch_queue.py      队列与历史记录
  forwarder.py           账号 / Bot 实际发送逻辑
  filters.py             规则匹配逻辑
  dashboard_actions.py   搜索和手动转发
  web/static/            前端静态页面
```

## 常见问题

### 提示 `simple mode requires TG_SOURCE_CHAT`

说明你还在使用单规则简化模式，但没有填源频道。

解决方式：

- 如果你用 Web 控制台，直接去“转发规则”里新增规则并保存
- 如果你坚持用单规则模式，就在 `.env` 里补全 `TG_SOURCE_CHAT`

### 规则修改后要不要重启

建议改完点一次“重启后端”，最省心。

### Bot 为什么收不到另一个 Bot 发的消息

Bot 之间不能像普通用户那样互相监听所有消息，这属于 Telegram 本身的权限限制。

如果你的核心需求是“稳定监听”，建议监听侧使用用户账号会话，发送侧再按策略选择 Bot 或用户。

## GitHub 发布前提醒

当前仓库已经忽略这些本地文件：

- `.env`
- `__pycache__`
- `*.pyc`
- `*.sqlite3`
- `*.db`
- `*.zip`
- IDE 配置目录

还差最后一个重要决定：

- `LICENSE` 还没有加

这个不能替你随便选，因为不同许可证会直接影响别人能否商用、修改、闭源再发布。

如果你确定要开源，常见选择是：

- `MIT`
- `Apache-2.0`
- `GPL-3.0`

你告诉我你想要哪一种，我可以马上帮你补上正式 `LICENSE` 文件。
