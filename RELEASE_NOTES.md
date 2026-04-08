# Release Notes

## 本轮交付内容

- **稳定性**
  - HDHive 自动签到支持指数退避重试（含抖动与每日重试上限）
  - 失败队列重试升级为智能重试（跳过不可重试错误、支持 FloodWait 冷却判断）
- **可观测性**
  - 新增健康检查接口：`/api/health` 与 `/api/v1/health`
  - 支持文件日志（按天滚动与保留天数）
  - 新增诊断包导出接口：`/api/diagnostics/export`
- **易用性**
  - 保存配置时增加 HDHive 签到必填项前置校验
  - 日志中的规则匹配说明增强（命中详情 / 未命中详情）
- **扩展性**
  - 规则新增 `group` / `priority`
  - 控制台支持分组启停、分组折叠、分组筛选、组内按优先级排序
  - 后端加载规则时按 `priority -> group -> name` 稳定排序

## 升级注意事项

- 旧配置可直接兼容，`group` 与 `priority` 缺省时会自动补默认值。
- 若启用文件日志，请确认部署目录有写权限。
- 建议在生产环境将 `TG_DASHBOARD_PASSWORD` 改为强密码，并通过 HTTPS 反代访问控制台。

## 主要接口变更

- `GET /api/health`：新增
- `GET /api/v1/health`：新增
- `GET /api/diagnostics/export`：新增
- `POST /api/queue/retry-failed`：行为增强（智能重试 + 跳过统计）
