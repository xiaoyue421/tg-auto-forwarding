# HDHive 单文件 CLI 使用教程

本文档对应同目录下的 `hdhive.py`，它是一个可直接命令行运行的单文件工具，整合了：

- `hdhive-openapi-docs` 的接口说明
- `hdhive-openapi-sdk-python` 的 Python 调用方式

支持 `meta/query/unlock/write/vip` 主要接口，以及 OAuth 换 Token 流程相关接口。

---

## 1. 准备环境

- Python 3.9+（建议）
- 已有 HDHive OpenAPI 的 `API Key`（或应用 Secret）
- 如调用需要用户身份的接口，还要有 `access_token`

本工具只使用 Python 标准库，不需要 `pip install` 任何第三方包。

---

## 2. 文件放置

把 `hdhive.py` 和本 `help.md` 放在同一目录即可，例如：

```powershell
E:\dm\新建文件夹\
  ├─ hdhive.py
  └─ help.md
```

---

## 3. 查看命令帮助

```powershell
python .\hdhive.py -h
```

查看某个子命令帮助：

```powershell
python .\hdhive.py resources -h
python .\hdhive.py share-create -h
```

---

## 4. 全局参数

所有子命令都支持以下通用参数：

- `--base-url`：API 地址，默认 `https://hdhive.com`
- `--api-key`：必填，OpenAPI API Key 或应用 Secret
- `--access-token`：可选，用户 Access Token（很多业务接口建议传）
- `--timeout`：请求超时时间（秒），默认 `30`
- `--pretty`：格式化输出 JSON

通用格式：

```powershell
python .\hdhive.py --api-key "your-api-key" --pretty <子命令> [参数]
```

---

## 5. 快速上手

### 5.1 健康检查

```powershell
python .\hdhive.py --api-key "your-api-key" --pretty ping
```

### 5.2 查配额

```powershell
python .\hdhive.py --api-key "your-api-key" --pretty quota
```

### 5.3 查用量（历史）

```powershell
python .\hdhive.py --api-key "your-api-key" --pretty usage --start-date 2026-04-01 --end-date 2026-04-25
```

### 5.4 查今日用量

```powershell
python .\hdhive.py --api-key "your-api-key" --pretty usage-today
```

---

## 6. 查询与解锁接口

### 6.1 按 TMDB 查询资源

```powershell
python .\hdhive.py --api-key "your-api-key" --access-token "user-access-token" --pretty resources --type movie --tmdb-id 550
```

### 6.2 查看分享详情

```powershell
python .\hdhive.py --api-key "your-api-key" --access-token "user-access-token" --pretty share --slug "a1b2c3d4e5f647898765432112345678"
```

### 6.3 检查资源链接类型

```powershell
python .\hdhive.py --api-key "your-api-key" --access-token "user-access-token" --pretty check-resource --url "https://115.com/s/example 访问码:1234"
```

### 6.4 解锁资源

```powershell
python .\hdhive.py --api-key "app-secret" --access-token "user-access-token" --pretty unlock --slug "a1b2c3d4e5f647898765432112345678"
```

---

## 7. 分享管理（write）

### 7.1 获取我的分享列表

```powershell
python .\hdhive.py --api-key "your-api-key" --access-token "user-access-token" --pretty shares --page 1 --page-size 20
```

### 7.2 创建分享

`--data` 传 JSON 对象（字符串）：

```powershell
python .\hdhive.py --api-key "your-api-key" --access-token "user-access-token" --pretty share-create --data "{\"url\":\"https://pan.example.com/s/abc\",\"tmdb_id\":\"550\",\"media_type\":\"movie\",\"title\":\"Fight Club\"}"
```

### 7.3 更新分享

```powershell
python .\hdhive.py --api-key "your-api-key" --access-token "user-access-token" --pretty share-patch --slug "a1b2c3d4e5f647898765432112345678" --data "{\"title\":\"新标题\",\"unlock_points\":5}"
```

### 7.4 删除分享

```powershell
python .\hdhive.py --api-key "your-api-key" --access-token "user-access-token" --pretty share-delete --slug "a1b2c3d4e5f647898765432112345678"
```

---

## 8. VIP 接口

### 8.1 获取当前用户信息

```powershell
python .\hdhive.py --api-key "app-secret" --access-token "user-access-token" --pretty me
```

### 8.2 每日签到

普通签到：

```powershell
python .\hdhive.py --api-key "app-secret" --access-token "user-access-token" --pretty checkin
```

高波动签到（`is_gambler=true`）：

```powershell
python .\hdhive.py --api-key "app-secret" --access-token "user-access-token" --pretty checkin --is-gambler true
```

### 8.3 长期 VIP 每周免费解锁状态

```powershell
python .\hdhive.py --api-key "app-secret" --access-token "user-access-token" --pretty weekly-free-quota
```

---

## 9. OAuth 相关命令

> 注意：用户授权入口通常应走浏览器授权页 `/openapi/authorize`。CLI 主要用于服务端调试和 token 交换。

### 9.1 预览授权信息

```powershell
python .\hdhive.py --api-key "app-secret" --pretty oauth-authorize-preview --client-id "app_xxx" --redirect-uri "https://client.example.com/callback" --scope "query unlock" --state "opaque-state"
```

### 9.2 授权码换 Token

```powershell
python .\hdhive.py --api-key "app-secret" --pretty oauth-exchange-code --code "authorization-code" --redirect-uri "https://client.example.com/callback"
```

### 9.3 刷新 Token

```powershell
python .\hdhive.py --api-key "app-secret" --pretty oauth-refresh --refresh-token "refresh-token"
```

### 9.4 撤销刷新令牌

```powershell
python .\hdhive.py --api-key "app-secret" --pretty oauth-revoke --refresh-token "refresh-token"
```

---

## 10. 作为 Python 模块使用（可选）

`hdhive.py` 也包含客户端类，可在你自己的脚本里导入：

```python
from hdhive import HDHiveClient

client = HDHiveClient("https://hdhive.com", "your-api-key").with_access_token("user-access-token")
resp = client.resources("movie", "550")
print(resp)
```

---

## 11. 错误处理建议

- HTTP 429 时，按服务端 `Retry-After` 与 `retry_after_seconds` 退避重试
- `OPENAPI_USER_REQUIRED`：说明当前命令需要用户身份，请补 `--access-token`
- `SCOPE_NOT_ALLOWED` / `USER_SCOPE_NOT_ALLOWED`：检查应用或用户 scope
- `VIP_REQUIRED`：接口需要 Premium 用户
- `INSUFFICIENT_POINTS`：解锁积分不足

命令失败时会输出结构化错误 JSON 到标准错误流，退出码为 `1` 或 `2`。

---

## 12. 常见问题

### Q1：为什么提示缺少 API Key？

因为所有命令都必须带 `--api-key`。

### Q2：哪些接口一定要 `--access-token`？

涉及具体用户业务动作时建议都带，例如资源解锁、用户信息、签到、分享写入等。

### Q3：PowerShell 里 JSON 字符串引号总是报错怎么办？

优先用双引号包裹整体，并把内部双引号转义成 `\"`（见上文 `share-create` 示例）。

---

## 13. 命令总览

- `ping`
- `quota`
- `usage`
- `usage-today`
- `resources`
- `unlock`
- `check-resource`
- `share`
- `shares`
- `share-create`
- `share-patch`
- `share-delete`
- `me`
- `checkin`
- `weekly-free-quota`
- `oauth-authorize-preview`
- `oauth-exchange-code`
- `oauth-refresh`
- `oauth-revoke`

---

## 14. `auto_unlock.py` 自动解锁脚本用法

`auto_unlock.py` 是基于 `hdhive.py` 客户端封装的自动判断+解锁脚本，逻辑如下：

- 先调用 `share --slug` 读取资源信息
- 默认仅在以下条件下自动解锁：
  - `unlock_message = "免费资源"`
  - `unlock_points = null`
- 如果开启了付费阈值模式，则当 `unlock_points <= 你设置的阈值` 也会自动解锁

### 14.1 查看帮助

```powershell
python .\auto_unlock.py -h
```

### 14.2 参数说明

- `--api-key`：必填，OpenAPI Key
- `--slug`：必填，资源 slug
- `--access-token`：可选，接口要求用户身份时传入
- `--allow-paid`：开启付费阈值解锁
- `--max-points`：付费阈值（例如 `4`）
- `--pretty`：JSON 美化输出
- `--url-only`：只输出最终解锁 URL（纯文本，便于脚本串联）

### 14.3 只解锁免费资源（默认）

```powershell
python .\auto_unlock.py --api-key "your-api-key" --slug "6ae66b45c67011ef87c60242ac120005" --pretty
```

### 14.4 开启积分阈值解锁（例如 <= 4）

```powershell
python .\auto_unlock.py --api-key "your-api-key" --slug "6ae66b45c67011ef87c60242ac120005" --allow-paid --max-points 4 --pretty
```

### 14.5 只输出 URL（用于后续脚本）

只要命中可解锁条件并解锁成功，就只输出 URL 文本：

```powershell
python .\auto_unlock.py --api-key "your-api-key" --slug "6ae66b45c67011ef87c60242ac120005" --url-only
```

搭配积分阈值：

```powershell
python .\auto_unlock.py --api-key "your-api-key" --slug "6ae66b45c67011ef87c60242ac120005" --allow-paid --max-points 4 --url-only
```

### 14.6 返回码说明

- `0`：脚本执行成功（并且在 `--url-only` 模式下成功输出 URL）
- `1`：OpenAPI 返回错误
- `2`：脚本内部异常
- `3`：`--url-only` 模式下不满足解锁条件（未输出 URL）

### 14.7 批量 slug 处理示例（PowerShell）

先准备一个 `slugs.txt`，每行一个 slug，例如：

```text
6ae66b45c67011ef87c60242ac120005
a1b2c3d4e5f647898765432112345678
```

#### 仅处理免费资源，成功时输出 URL

```powershell
Get-Content .\slugs.txt | ForEach-Object {
  $slug = $_.Trim()
  if (-not $slug) { return }
  python .\auto_unlock.py --api-key "your-api-key" --slug $slug --url-only
  if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] $slug"
  } elseif ($LASTEXITCODE -eq 3) {
    Write-Host "[SKIP] $slug (不满足解锁条件)"
  } else {
    Write-Host "[ERR] $slug (exit=$LASTEXITCODE)"
  }
}
```

#### 开启积分阈值（<= 4）批量处理

```powershell
Get-Content .\slugs.txt | ForEach-Object {
  $slug = $_.Trim()
  if (-not $slug) { return }
  python .\auto_unlock.py --api-key "your-api-key" --slug $slug --allow-paid --max-points 4 --url-only
  if ($LASTEXITCODE -eq 0) {
    Write-Host "[OK] $slug"
  } elseif ($LASTEXITCODE -eq 3) {
    Write-Host "[SKIP] $slug (积分超阈值或不满足免费规则)"
  } else {
    Write-Host "[ERR] $slug (exit=$LASTEXITCODE)"
  }
}
```


