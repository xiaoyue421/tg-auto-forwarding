# TelegramForwarder

一个带中文 Web 控制台的 Telegram 转发工具。

它支持多规则实时监听、多源到多目标转发、账号 / Bot 多种发送策略、历史消息搜索、失败队列和转发监控，适合需要稳定筛选与分发 Telegram 消息的场景。

## 功能亮点

- 中文 Web 控制台，适合直接在浏览器里管理配置
- 多规则同时运行
- 多源到多目标、多对一转发
- 支持账号目标和 Bot 目标同时配置
- 支持规则级发送策略
- 支持关键词、正则、黑名单过滤
- 支持 HDHive 资源链接识别与直链转发（可选自动解锁）
- 支持“需要媒体”和“需要文本内容”组合判断
- 支持历史消息搜索后手动指定转发
- 支持本地发送队列、失败重试、成功历史去重
- 支持失败任务智能重试（自动跳过不可重试错误、FloodWait 冷却）
- 支持规则 `group` / `priority`（经配置 JSON 或 API；后端按优先级排序）
- 支持自动签到重试策略（指数退避 + 抖动 + 每日上限）
- 支持健康检查与一键导出诊断包（脱敏配置 + 状态 + 日志）
- 支持可选文件日志（按天滚动）
- 支持 Docker 部署

## 适用场景

- 监听频道 / 群里的消息并按规则转发
- 将不同来源的资源消息集中整理到目标群
- 按关键词、正则或 HDHive 资源链接自动筛选
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

- Python `>= 3.11`（与 Docker 镜像默认 `3.12` 均支持，推荐与生产环境保持一致）
- 一个有效的 Telegram `API ID` 和 `API HASH`
- 一个已登录的用户账号 `session_string`
- 如果要用 Bot 发送，还需要一个或多个 Bot Token

建议：

- 监听建议优先使用用户账号
- Bot 更适合做“发送端”，不适合作为通用监听端

## 快速开始

### 1. 安装依赖

```powershell
pip install -e .
```

开发环境（含 ruff）：

```powershell
pip install -e ".[dev]"
```

### 2. 构建 Web 前端（Vite + Vue 3）

控制台 UI 源码在 `frontend/`，构建后写入 `src/tg_forwarder/web/static/`。**本地直接跑 `tg_forwarder web` 前请先构建一次**（使用 Docker 镜像时可跳过，镜像构建阶段会自动执行 `npm run build`）。

```powershell
cd frontend
npm install
npm run build
cd ..
```

前端开发（热更新 + 将 `/api` 代理到本机 8080）：

```powershell
cd frontend
npm install
npm run dev
```

另开终端启动后端：`python -m tg_forwarder web --config .env --host 127.0.0.1 --port 8080`，浏览器访问 Vite 提示的地址（默认 `http://127.0.0.1:5173`）。

更多说明见 `frontend/README.md`。

### 3. 准备 `.env`

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

新增常用可选项（建议按需开启）：

- `TG_FILE_LOG_ENABLED=true`：开启文件日志
- `TG_FILE_LOG_PATH=logs/tg_forwarder.log`：文件日志路径
- `TG_FILE_LOG_RETENTION_DAYS=7`：日志保留天数（按天滚动）
- `HDHIVE_CHECKIN_MAX_RETRIES=4`：自动签到每日最大尝试次数（含首次）
- `HDHIVE_CHECKIN_RETRY_BASE_SECONDS=60`：自动签到退避基准秒数
- `HDHIVE_CHECKIN_RETRY_MAX_SECONDS=1800`：自动签到退避最大秒数
- `HDHIVE_CHECKIN_RETRY_JITTER_SECONDS=15`：自动签到重试抖动秒数

### 4. 启动 Web 控制台

```powershell
python -m tg_forwarder web --config .env --host 0.0.0.0 --port 8080
```

浏览器打开 `http://127.0.0.1:8080`，使用 `.env` 中的 `TG_DASHBOARD_PASSWORD` 登录（默认 `admin`，上线前请修改）。

### 5. 登录 Telegram 账号

任选其一即可，成功后都会写入 `.env` 的 `TG_SESSION_STRING`：

| 方式 | 操作 |
|------|------|
| **网页（推荐）** | 控制台 → **基础配置** →「网页登录 Telegram」：验证码或扫码 |
| **命令行** | `python -m tg_forwarder login --config .env --save-env` |

监听转发必须使用**用户账号**会话；Bot Token 仅用于发送端（见「发送策略」）。

---

## 使用说明

下面是从「装好依赖」到「稳定转发」的完整操作指引；日常维护以 **Web 控制台** 为主即可。

### 推荐流程（首次配置）

1. **准备 `.env`**：复制 `.env.example`，填写 `TG_API_ID`、`TG_API_HASH`、`TG_DASHBOARD_PASSWORD`。
2. **构建前端**（本地非 Docker 时）：`cd frontend && npm install && npm run build`。
3. **启动控制台**：`python -m tg_forwarder web --config .env --host 127.0.0.1 --port 8080`。
4. **登录控制台**：浏览器打开控制台地址，输入控制台密码。
5. **登录 Telegram**：在 **基础配置** 用验证码或扫码写入 `session_string`（见上文第 5 步）。
6. **（可选）填写 Bot Token**：多个 Token 用英文逗号分隔；Bot 需已加入目标群并有发言权限。
7. **配置转发规则**：在 **规则设置** 新增规则，填写源频道、账号/Bot 目标、关键词/正则等。
8. **保存并校验**：顶栏 **保存配置** → **校验配置**，在 **运行与队列** 查看校验出的 Worker 列表。
9. **启动监听**：顶栏 **启动后端**；侧边栏「服务」变为运行中，Worker 卡片显示「运行中」。
10. **验证转发**：用**另一个账号**往源群发测试消息（默认不转发本账号自己发的消息），在 **系统日志** / 目标群确认。

修改规则、Bot、代理等后建议：**保存配置** → **校验配置** → **重启后端**。

Docker / Web 重启后默认会**自动启动转发进程**（`.env` 中 `TG_AUTO_START_WORKERS=true`，设为 `false` 则仍需在控制台手动点「启动后端」）。

### 命令行工具

安装 `pip install -e .` 后可用：

| 命令 | 说明 |
|------|------|
| `python -m tg_forwarder web -c .env --host 0.0.0.0 --port 8080` | 启动 Web 控制台（主入口） |
| `python -m tg_forwarder login -c .env --save-env` | 交互式生成 `TG_SESSION_STRING` 并写回 `.env` |
| `python -m tg_forwarder validate -c .env` | 校验 `.env` / 规则，打印将运行的 Worker |
| `python -m tg_forwarder run -c config.yaml` | 无 Web、仅用 YAML 配置跑转发（高级） |
| `tg-hdhive-unlock "https://hdhive.com/resource/…"` | 按站点设置测试 HDHive 自动解锁（读 `.env`） |

等价入口：`tg-forwarder web`（`pyproject.toml` 中注册的脚本名）。

### 配置模式

| 模式 | 适用场景 | 配置位置 |
|------|----------|----------|
| **多规则（推荐）** | 多源多目标、每条规则独立过滤 | 控制台 **规则设置**，保存后写入 `TG_RULES_JSON` |
| **单规则简化** | 仅一对源/目标、不用控制台改规则 | `.env` 中 `TG_SOURCE_CHAT`、`TG_TARGET_CHATS` 等 |

多规则模式下，`.env` 里的 `TG_SOURCE_CHAT` / `TG_TARGET_CHATS` 可仅作占位；若未配置任何规则且未填源频道，校验会提示 `simple mode requires TG_SOURCE_CHAT`。

### 频道 / 群怎么写

在规则或 `.env` 中，源与目标可使用：

- `@channel_username` 或 `@group_username`
- 数字 ID（如 `-1001234567890`）
- `https://t.me/xxx` 形式链接（系统会解析）

多个源：在规则里用**英文逗号、分号或换行**分隔。多个目标：账号目标、Bot 目标字段内用**英文逗号**分隔。

监听账号须已加入源群/频道；发往目标时，登录账号或 Bot 也须在对应目标中，且具备发消息权限。

### 控制台分区说明

登录后左侧为工作区导航；顶栏固定有 **保存 / 校验 / 导出 / 导入配置** 与 **启动 / 重启 / 停止后端**。

| 分区 | 作用 |
|------|------|
| **基础配置** | API ID/HASH、网页或手动 session、Bot Token、全局发送策略、全局限流、启动通知、代理 |
| **站点设置** | HDHive 签到（Premium API Key / 非 Premium 网页账号）、自动签到、资源自动解锁、测试签到与转发路径检测 |
| **模块** | 导入 `.zip` 或放置带 `module.json` 的扩展；`hooks.py` 的 `after_match` 在规则匹配后执行（改 hooks 需**重启后端**） |
| **规则设置** | 多规则：源/目标、发送策略、关键词/正则、HDHive 直链、Telegraph（tgph）页面解析、媒体与文本条件 |
| **消息搜索** | 对已配置源做关键词模糊搜索，支持按规则目标**手动转发**历史消息 |
| **运行与队列** | Worker / Dispatcher 状态、失败队列智能重试、成功转发去重历史、校验结果 |
| **系统日志** | 全部日志、HDHive 签到、转发监测、实时检测、错误；支持按来源筛选 |

**配置导入/导出**：顶栏可导出当前配置 JSON 备份，或从 JSON 导入（适合迁移、多机同步）。敏感字段请自行脱敏后再分享。

**会话说明**：控制台登录使用 HTTP-only Cookie（`tg_dashboard_session`），刷新页面无需在浏览器存密码；Telegram 的 `session_string` 保存在服务端 `.env`。

### 本地目录与数据文件

| 路径 | 说明 |
|------|------|
| `.env` | 主配置；控制台「保存配置」会写回此文件（Docker 挂载须**可写**） |
| `data/` | Docker 默认数据卷挂载点：队列库 `tg_forwarder_queue.sqlite3` 等（对应容器 `/data`） |
| `scripts/` | 扩展模块目录（`TG_MODULES_PATH`，Compose 默认 `/workspace/scripts`） |
| `logs/` | 开启 `TG_FILE_LOG_ENABLED=true` 后的按天滚动日志 |
| `frontend/` | 控制台 UI 源码；`npm run build` 产出到 `src/tg_forwarder/web/static/` |

队列路径由 `TG_QUEUE_DB_PATH` 控制；本地直接运行时默认为项目下的路径，Docker 默认为 `/data/tg_forwarder_queue.sqlite3`。

### 仅命令行运行（不用 Web）

若已有 `config.yaml`（YAML 多 Worker 配置），可：

```powershell
python -m tg_forwarder validate -c config.yaml
python -m tg_forwarder run -c config.yaml
```

日常仍推荐使用 Web：规则、队列、日志与 HDHive 均在控制台内完成。

## Docker 部署

### 1. 构建并启动

```powershell
docker compose up -d --build
```

### 2. 打开控制台

Compose 将容器 **8080** 映射到主机 **8810**（见 `docker-compose.yaml`）：

```text
http://127.0.0.1:8810
```

### 3. 登录 Telegram 与会话

任选其一：

| 方式 | 操作 |
|------|------|
| **网页（推荐）** | 浏览器打开 `http://127.0.0.1:8810` → 控制台密码 → **基础配置** 内扫码/验证码登录 |
| **容器内命令行** | 见下方 `docker compose run … login` |

生产服务名为 **`tg-forwarder`**（`docker compose up -d` 默认启动，无需 `COMPOSE_PROFILES`）：

```powershell
docker compose run --rm tg-forwarder python -m tg_forwarder login --config /workspace/.env --save-env
```

然后在控制台配置规则并 **启动后端**（与本地使用方式相同）。

### 4. 当前 Docker 说明（`docker-compose.yaml`）

| 服务 | 默认是否启动 | 主机端口 | 说明 |
|------|--------------|----------|------|
| `tg-forwarder` | 是 | **8810** → 8080 | 生产：镜像内代码；挂 `./.env`（须**可写**）、`./data`、`./scripts` |
| `tg-forwarder-dev` | 否 | **8081**（`TG_DASHBOARD_DEV_PORT`） | 开发：`.env` 中 `COMPOSE_PROFILES=true`；挂载整个项目目录 |

开发时建议先停生产再只起开发，避免同一 Telegram 会话双实例：

```powershell
docker compose stop tg-forwarder
$env:COMPOSE_PROFILES='true'; docker compose up -d --build
```

恢复生产：`docker compose stop tg-forwarder-dev` 后执行 `docker compose up -d`。

- 生产改代码需 **`docker compose build`** 再启动；改 `frontend/` 也需重新 build 镜像。
- 内置 `healthcheck`（`GET /api/health`）；队列库在 `./data`（容器 `/data`），默认 SQLite **WAL**。
- 扩展模块默认目录：`./scripts`（环境变量 `TG_MODULES_PATH=/workspace/scripts`）。

## Web 控制台说明

更完整的分区说明见上文 [控制台分区说明](#控制台分区说明)。本节补充字段含义、HDHive 与运维接口。

### 安全说明

- 控制台默认仅依赖密码；登录成功后会下发 **HTTP-only** 会话 Cookie（`tg_dashboard_session`），刷新页面无需再把密码存进浏览器 `localStorage`。
- 连续登录失败会触发 **限速**（见 `.env.example` 中 `TG_DASHBOARD_LOGIN_*`）。
- **不要**将控制台直接暴露到公网；若必须远程访问，请放在 HTTPS 反向代理后，并设置 `TG_DASHBOARD_COOKIE_SECURE=true`。
- 未配置 `TG_DASHBOARD_CORS_ORIGINS` 时 **不启用 CORS**，适合浏览器与 API 同源访问；跨域场景请显式填写允许的来源列表。

### 基础配置（要点）

| 项 | 说明 |
|----|------|
| API ID / HASH | 从 [my.telegram.org](https://my.telegram.org) 获取 |
| 网页登录 Telegram | 验证码或扫码，成功后自动写入 `SESSION STRING` |
| BOT TOKEN | 可选；多个用英文逗号分隔，按 bot#1 → bot#2 顺序尝试 |
| 转发策略 | 全局默认；单条规则可设为「跟随全局」或覆盖 |
| 全局限流 | 多规则同时命中时共用一个发送间隔，降低 FloodWait 风险 |
| 启动通知 | 启动/重启后向已配置目标发一条通知（可自定义 HTML） |
| 代理 | `TG_PROXY_*` 或 `TG_PROXY_URLS`；HDHive 可共用或单独配置 |

Bot 会话目录：配置 `TG_BOT_SESSION_DIR` 后，Bot 使用磁盘 SQLite 会话，减轻重启时的 `ImportBotAuthorization` 限流。

### 转发规则（要点）

每条规则可独立配置：

- 源（多个）、账号目标、Bot 目标、规则级发送策略（或跟随全局）
- 关键词：任一命中 / 必须全部 / 黑名单；正则同理（一行一个）
- **HDHive**：识别 `hdhive.com/resource/…` 并转发直链；可要求同时命中本规则关键词
- 媒体/文本：`需要媒体`、`需要文本` 及 `all` / `any` 组合；**区分大小写**
- 监听编辑、转发自己消息（测试用，注意避免源=目标造成循环）

规则 JSON 中还可包含 `group`、`priority`（导入配置或 API 写入）；后端按 `priority → group → name` 排序。控制台 UI 以关键词/正则/HDHive 选项为主。

### 站点设置（HDHive）

与 **规则** 里 HDHive 直链转发配合使用：

1. 选择签到方式：Premium（API Key）或非 Premium（网页用户名+密码，走 `hdhive_site_login_checkin.py`）。
2. 配置 API Key、Cookie、自动签到与代理开关。
3. 配置「自动解锁回退」积分上限（与转发 Worker 一致）。
4. **测试签到** / **立即签到**、**检测转发路径**（不扣积分）、**真实解锁测试**（会扣积分）。

签到与解锁是不同接口：签到失败时，只要 API Key 对分享/解锁有效，转发仍可能获取直链。

### 扩展模块

- 目录：默认 `scripts/`（或 `.env` 中 `TG_MODULES_PATH`）。
- **导入模块 (.zip)**：包内需含 `module.json`；可选 `hooks.py`（`after_match`）、`web/index.html`（模块界面）。
- 修改 `hooks.py` 后必须 **重启后端**；改 `config.json` 可通过模块界面或 API 保存。

### 历史搜索

从**所有已配置源**做关键词模糊搜索，范围仅 Telegram 原消息：正文、caption、按钮文字、消息内链接文本。

支持按频道筛选、**按已配置目标转发**、打开原消息链接。搜索模式由 `TG_SEARCH_DEFAULT_MODE` 控制（当前为 `fast`）。

### 运行与队列、日志

| 能力 | 说明 |
|------|------|
| Worker 状态 | 每条启用规则对应一个监听进程；连续失败过多会暂停 |
| Dispatcher | 本地 SQLite 队列；重启后继续未完成任务 |
| 失败队列 | **重试失败任务** 为智能重试（跳过不可重试错误、FloodWait 冷却） |
| 成功历史 | 按规则去重，可清空单规则或全部 |
| 系统日志 | 筛选：全部 / HDHive 签到 / 转发监测 / 实时检测 / 错误 |

### 健康检查与诊断（API）

- `GET /api/health`、`GET /api/v1/health`：服务状态、队列、HDHive 签到等
- `GET /api/diagnostics/export`：脱敏配置 + 状态 + 失败样本 + 近期日志（JSON）
- `GET /api/logs`：内存日志；`before_sequence` 分页拉取更早记录

### 控制台前端如何构建

`frontend/vite.config.ts` 将构建产物输出到 `src/tg_forwarder/web/static`。修改 `frontend/src` 后：

```powershell
cd frontend
npm install
npm run build
```

再启动 Web 或重新 `docker compose build`。详见 `frontend/README.md`。

### 故障排查（运维）

- **Bot 登录出现 `FloodWaitError` / `ImportBotAuthorization` 限流**：设置 `TG_BOT_FLOODWAIT_MAX_SLEEP_SECONDS` 略大于 Telegram 提示秒数；增大 `TG_BOT_POOL_START_STAGGER_SECONDS`；配置 `TG_BOT_SESSION_DIR`。
- **日志显示已转发但目标频道看不到**：`account_first` 会优先用登录账号投递，成功时可能不再用 Bot；查看「策略转发摘要」中 `本轮投递=`。
- **Docker 控制台打不开**：确认访问 **8810**（非 8080）；`docker compose ps` 与 `docker compose logs -f tg-forwarder`。
- **保存配置失败**：检查 `.env` 挂载是否可写；每行须为 `KEY=value` 或注释，勿粘贴裸 URL 行（见 `.env.example` 顶部说明）。
- **队列库损坏**：备份后删除或重建 `TG_QUEUE_DB_PATH` 指向的 sqlite 文件（见 `.env.example` 中 `TG_QUEUE_DB_PATH` 注释）。

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

### 命中任一关键词 / 任一正则

这一组只要命中一个即可。

示例：

```text
命中任一关键词：
- ed2k
- magnet
- 115cdn
```

消息里只要出现其中一个，就算通过这组条件。

若规则开启 **HDHive 直链转发**，消息中出现 `hdhive.com/resource/…` 时会尝试解析为可转发直链（可与关键词/正则组合，见规则页说明）。

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

1. 在 **基础配置** 完成 Telegram 登录（或 CLI `login`）。
2. 确保监听账号已加入**源**与**目标**；若测 Bot 发送，将 Bot 拉入目标群并授权。
3. **规则设置** 新增规则 → 顶栏 **保存配置** → **校验配置** → **启动后端**。
4. 用**另一个账号**往源群发测试消息（勿用监听账号自己发，除非开启「转发自己发送的消息」）。
5. 在 **系统日志**（转发监测）与目标群确认；**运行与队列** 可看 Worker 与 Dispatcher。

注意：源与目标相同且开启「转发自己消息」时可能循环转发。

### 测试历史搜索和指定转发

1. 打开 **消息搜索**，输入关键词 → **搜索所有源**。
2. 按频道筛选结果 → **按已配置目标转发** 或打开原消息。

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

## Telegraph 解析（`tgph/`，独立于 HDHive）

当消息带有 **[telegra.ph](https://telegra.ph/)** 文章链接时，由 `tgph/` **单独**拉取文章 HTML，用规则里的关键词 / 正则对**页面内容**判断（不是对 Telegram 消息正文，也与 HDHive 站点设置无关），再提取文内直链（如 `115cdn.com/s/…`）作为转发正文。

| 模块 | 作用 |
|------|------|
| `tgph/fetch.py` | 拉取 Telegraph HTML |
| `tgph/content.py` | 从 HTML 提取可检索正文 |
| `tgph/match.py` | 按规则对**页面 HTML** 做关键词 / 正则匹配 |
| `tgph/resolve.py` | `resolve_tgph_dispatch_text` — 匹配通过后提取直链 |
| `tgph/cli.py` | 命令行自测页面匹配与直链提取 |

**规则示例**（[示例文章](https://telegra.ph/星辰光辉更胜太阳-更新412集-1080P-amzn源-官方简体中文字幕332GB神秘G-10-24)）：

- 勾选 **Telegraph：解析 telegra.ph 文章 HTML 并转发文内直链**
- 勾选 **仅当 Telegraph 页面 HTML 命中下方关键词/正则时才转发**
- **命中任一关键词** 填 `115cdn` 或 `4K`（须出现在文章 HTML 中）

详见 [`tgph/RULE_EXAMPLE.md`](tgph/RULE_EXAMPLE.md)。

```powershell
$env:PYTHONPATH = "src;."
python -m tgph.cli --require-match --keyword 115cdn --pretty "https://telegra.ph/你的文章路径"
```

Docker / 本地需 `PYTHONPATH` 含仓库根目录（Compose：`/workspace/src:/workspace`）。

拉取 `telegra.ph` 会复用 **基础配置** 里的 `TG_PROXY_*`（与 Telegram 相同）。容器内若出现 `Network is unreachable`，请在控制台填好代理并保存配置。

---

## 仓库内 HDHive 工具（`hdhive/`）

目录 **`hdhive/`** 提供与 [HDHive](https://hdhive.com) 相关的独立脚本与小型 Python 包；**控制台里的签到、资源解析与自动解锁** 仍主要通过 `.env` 与 `tg_forwarder` 完成（见上文「快速开始」中的 `HDHIVE_*` 与站点设置页）。原独立文档 **`hdhive/help.md`** 已合并到本节，发布单仓时无需再维护该文件。

### `hdhive_site_login_checkin.py`（非 Premium：网页登录 + 签到）

- **用途**：用户名/邮箱 + 密码经站点 Server Action 登录，并在登录态下执行首页签到；与 Web 控制台「非 Premium」**测试签到 / 立即签到**、以及 Worker 定时签到（cookie 模式）使用同一子进程脚本。
- **运行**：在仓库根执行 `python hdhive/hdhive_site_login_checkin.py`（可从当前目录或上级目录的 `.env` 读取 `HDHIVE_LOGIN_*`；亦支持 `--username` / `--password`）。Docker 中可通过 `HDHIVE_SITE_LOGIN_SCRIPT` 指向副本路径。
- **环境变量**：见 `.env.example` 中的 `HDHIVE_LOGIN_*`、`HDHIVE_CHECKIN_*`、代理相关键。

### `hdhive.py`（OpenAPI CLI，仅标准库）

单文件封装 HDHive OpenAPI 常用能力（查询、解锁、分享写入、VIP、OAuth 辅助等），**不依赖**除标准库外的 pip 包。

**全局参数**（各子命令通用）：

| 参数 | 说明 |
|------|------|
| `--base-url` | API 根地址，默认 `https://hdhive.com` |
| `--api-key` | 必填，OpenAPI Key 或应用 Secret |
| `--access-token` | 可选；涉及用户身份的业务（解锁、签到、分享写入等）建议传入 |
| `--timeout` | 超时秒数，默认 `30` |
| `--pretty` | 将 JSON 输出格式化 |

**常用命令**（更多子命令与参数请 `-h`）：

```powershell
python hdhive/hdhive.py -h
python hdhive/hdhive.py --api-key "YOUR_KEY" --pretty ping
python hdhive/hdhive.py --api-key "YOUR_KEY" --pretty quota
python hdhive/hdhive.py --api-key "KEY" --access-token "TOKEN" --pretty usage-today
python hdhive/hdhive.py --api-key "KEY" --access-token "TOKEN" --pretty checkin
python hdhive/hdhive.py --api-key "KEY" --access-token "TOKEN" --pretty unlock --slug "YOUR_SLUG"
```

**子命令一览**：`ping`、`quota`、`usage`、`usage-today`、`resources`、`unlock`、`check-resource`、`share`、`shares`、`share-create`、`share-patch`、`share-delete`、`me`、`checkin`、`weekly-free-quota`、`oauth-authorize-preview`、`oauth-exchange-code`、`oauth-refresh`、`oauth-revoke`。

**作为 Python 模块**（开发安装 `pip install -e .` 后，在含包根的路径下）：

```python
from hdhive import HDHiveClient

client = HDHiveClient("https://hdhive.com", "your-api-key").with_access_token("user-access-token")
resp = client.resources("movie", "550")
print(resp)
```

**错误与重试**：遇 HTTP 429 应按响应中的 `Retry-After` 或 JSON 里的 `retry_after_seconds` 退避；`OPENAPI_USER_REQUIRED` 表示需 `--access-token`；`INSUFFICIENT_POINTS` 表示解锁积分不足。失败时 stderr 常含结构化错误 JSON，进程退出码为 `1` 或 `2`。

### `auto_unlock.py`（按 slug 先查 share 再决定是否 unlock）

在 `hdhive.py` 客户端之上封装「先 `share`、再按免费/积分规则判断是否 `unlock`」。默认仅在免费资源条件下自动解锁；使用 `--allow-paid --max-points N` 时，若 `unlock_points <= N` 也会尝试解锁。

```powershell
python hdhive/auto_unlock.py -h
python hdhive/auto_unlock.py --api-key "KEY" --slug "SLUG" --pretty
python hdhive/auto_unlock.py --api-key "KEY" --slug "SLUG" --allow-paid --max-points 4 --url-only
```

**退出码**：`0` 成功；`1` OpenAPI 业务错误；`2` 脚本内部异常；`3`（仅 `--url-only`）不满足解锁条件因而未输出 URL。批量处理多 slug 时可用 shell 逐行读取 slug 文件并循环调用上述命令。

### `unlock_core.py`

与 `tg_forwarder.hdhive_unlock_core` 对齐的判定与解析逻辑，供 `auto_unlock.py` 与转发链路中的「HDHive 自动解锁」策略复用。

---

## 项目结构

```text
frontend/                Vite + Vue 3 控制台源码（npm run build → web/static）
tgph/                    Telegraph 独立解析（telegra.ph HTML 匹配 → 文内直链）
hdhive/                  HDHive：网页签到、OpenAPI CLI（hdhive.py）、auto_unlock.py
data/                    Docker 默认数据目录（队列库等，挂载为 /data）
scripts/                 扩展模块目录（TG_MODULES_PATH，可 zip 导入）
src/tg_forwarder/
  cli.py                 命令行：web / login / validate / run
  webapp.py              FastAPI 控制台与 REST API
  supervisor.py          Worker 进程管理
  worker.py              实时监听与规则匹配
  dispatcher.py          发送队列调度
  dispatch_queue.py      队列与成功历史
  forwarder.py           账号 / Bot 发送
  filters.py             关键词、正则、媒体文本匹配
  modules/               扩展模块加载（hooks.after_match）
  dashboard_actions.py   历史搜索与手动转发
  hdhive_*.py            HDHive 签到、资源解析与解锁
  web/static/            构建后的控制台静态资源
docker-compose.yaml      生产 8810、开发 8081（profile）
.env.example             环境变量说明模板
```

## 开源发布前建议

发布到 GitHub 前，建议再确认这几项：

- 不要提交 `.env`
- 不要提交 `session`、`session-journal`
- 不要提交 `*.sqlite3`、`*.db`
- 不要提交运行日志和临时压缩包
- 把 `TG_DASHBOARD_PASSWORD` 改成自己的密码
- 如果要公开演示，请先清理真实频道、群组和 Bot 信息

## 发布与验收

- 发布说明见：`RELEASE_NOTES.md`
- 回归清单见：`QA_CHECKLIST.md`

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

- [LICENSE](LICENSE)
