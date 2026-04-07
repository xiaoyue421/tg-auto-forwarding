#!/bin/sh
set -e
# COMPOSE_PROFILES=true 挂载整个 ./ 时，本机 static 可能只有占位页、无 Vite 的 assets/。
# 启动时从镜像内备份补全，即可直接打开控制台；本机 npm run build 后因有 assets 不再覆盖。
STATIC=/workspace/src/tg_forwarder/web/static
BAKED=/opt/tg-forwarder-dashboard-static
if [ ! -d "$BAKED" ]; then
  exec "$@"
fi

need_seed=0
if [ ! -d "$STATIC/assets" ]; then
  need_seed=1
elif [ -z "$(ls -A "$STATIC/assets" 2>/dev/null)" ]; then
  need_seed=1
fi
# 仅有占位 index、无真实构建时也可能出现「有 assets 目录但为空」以外的情况：检测占位文案
if [ "$need_seed" -eq 0 ] && [ -f "$STATIC/index.html" ]; then
  if python -c "
import pathlib
p = pathlib.Path('${STATIC}/index.html')
t = p.read_text(encoding='utf-8', errors='replace')
raise SystemExit(0 if '尚未构建' in t else 1)
" 2>/dev/null; then
    need_seed=1
  fi
fi

if [ "$need_seed" -eq 1 ]; then
  mkdir -p "$STATIC"
  cp -a "$BAKED"/. "$STATIC"/
fi
exec "$@"
