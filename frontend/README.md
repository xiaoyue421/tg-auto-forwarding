# TG 转发控制台前端（Vite + Vue 3）

源码在 `src/`，构建产物输出到 `../src/tg_forwarder/web/static/`（供 FastAPI 静态托管）。

## 常用命令

```bash
cd frontend
npm install
npm run dev
```

开发时 Vite 默认 `http://127.0.0.1:5173`，已将 `/api` 代理到 `http://127.0.0.1:8080`，需另起后端：

```bash
python -m tg_forwarder web --config .env --host 127.0.0.1 --port 8080
```

生产构建（写入 Python 包内 static 目录）：

```bash
npm run build
```

修改 UI 后请先执行 `npm run build`，再安装/运行 Python 包；Docker 镜像构建时会自动执行该步骤。

## 结构说明

- `public/`：构建时原样复制到产物根目录（如 `favicon.svg`）。
- `src/App.vue` + `src/dashboardApp.js`：控制台逻辑（Vue 3 Options API）。
