#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
trap cleanup_temps EXIT

MODE=deploy
UPDATE=0
SKIP_STATIC=0
SKIP_BUILD=0
SKIP_MIGRATE=0
REF=main
SHA=

usage() {
  cat <<'EOF'
用法:
  jobs/deploy.sh --preflight-only
  jobs/deploy.sh
  jobs/deploy.sh --update [--ref main | --sha 40位SHA]
  jobs/deploy.sh --start-only
只使用 jobs/state/compose.runtime.yml，不叠加 docker-compose.yml 或 override。
不发布 8000/5432/6379/3000，不重建 db/redis 容器，不删除数据库卷，不修改 Nginx。
不要求 Compose 2.24，不升级 Docker。端口写在单独文件里，不和仓库默认端口合并。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preflight-only) MODE=preflight ;;
    --start-only) MODE=start ;;
    --update) UPDATE=1 ;;
    --skip-static) SKIP_STATIC=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --skip-migrate) SKIP_MIGRATE=1 ;;
    --ref) REF=${2:-}; shift ;;
    --sha) SHA=${2:-}; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
  shift
done
if [[ "$MODE" == "preflight" && "$UPDATE" == "1" ]]; then
  die "--preflight-only 不能和 --update 一起使用"
fi
if [[ "$MODE" == "start" && "$UPDATE" == "1" ]]; then
  die "--start-only 不能和 --update 一起使用"
fi

require_cmd flock
acquire_lock

service_id() {
  local svc="$1" id
  id=$(docker ps -q \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter "label=com.docker.compose.service=${svc}" \
    --filter "label=com.docker.compose.oneoff=False" \
    --filter status=running)
  [[ -n "$id" && "$id" != *$'\n'* ]] || die "${svc} 没有处于单独的运行状态"
  printf '%s\n' "$id"
}

assert_db_redis_unchanged() {
  local file
  file=$(mktemp)
  chmod 600 "$file"
  collect_inspect "$file"
  py checks.py unchanged --contract "$ROOT/jobs/state/contract.json" --inspect "$file"
  rm -f "$file"
}

wait_api() {
  local deadline=$((SECONDS + 180)) api_id status body
  while (( SECONDS < deadline )); do
    api_id=$(docker ps -q \
      --filter "label=com.docker.compose.project=${PROJECT}" \
      --filter "label=com.docker.compose.service=api" \
      --filter "label=com.docker.compose.oneoff=False" \
      --filter status=running || true)
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$api_id" 2>/dev/null || true)
    body=$(py checks.py fetch-health --url "http://127.0.0.1:8010/api/v1/health" --timeout 5 2>/dev/null || true)
    if [[ -n "$api_id" && "$api_id" != *$'\n'* && "$status" == "healthy" ]] && printf '%s' "$body" | py checks.py health-body; then
      return 0
    fi
    sleep 5
  done
  die "API 在 180 秒内未就绪。要求 http://127.0.0.1:8010/api/v1/health 的 JSON 为 status=healthy 且 database=true，并且容器健康检查为 healthy。db/redis 容器不应被重建。"
}

build_static() {
  local static="/var/www/zhengaihe/crawler"
  [[ -f "$ROOT/web/package-lock.json" ]] || die "缺少 web/package-lock.json，拒绝 npm install"
  [[ -f "$ROOT/web/package.json" ]] || die "缺少 web/package.json"
  if [[ "${CRAWLER_TESTING:-}" == "1" ]]; then
    static=${CRAWLER_STATIC_ROOT:?测试静态目录未设置}
  fi
  STATIC_STAGE=$(mktemp -d)
  chmod 700 "$STATIC_STAGE"
  local -a npm_env=()
  if [[ -n "${NPM_REGISTRY:-}" ]]; then
    npm_env+=(-e "NPM_CONFIG_REGISTRY=${NPM_REGISTRY}")
  fi
  local node_image
  node_image=$(py checks.py base-image --kind node)
  if ! timeout 900 docker run --rm \
      -v "$ROOT/web:/src:ro" \
      -v "$STATIC_STAGE:/out" \
      -e VITE_BASE=/crawler/ \
      -e NPM_CONFIG_UPDATE_NOTIFIER=false \
      -e NPM_CONFIG_FUND=false \
      -e NPM_CONFIG_AUDIT=false \
      -e NPM_CONFIG_FETCH_RETRIES=3 \
      -e NPM_CONFIG_FETCH_TIMEOUT=60000 \
      -e NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=120000 \
      "${npm_env[@]}" \
      "$node_image" \
      bash -ceu 'set -o pipefail
        . /etc/os-release
        test "$VERSION_CODENAME" = bookworm
        test "$VERSION_ID" = 12
        test -f /src/package-lock.json
        mkdir -p /tmp/web
        tar -C /src -cf - . | tar -C /tmp/web -xf -
        cd /tmp/web
        npm ci --ignore-scripts --no-audit --no-fund
        npm run build
        test -s dist/index.html
        grep -q "/crawler/" dist/index.html
        if grep -R -n "localhost:8000" dist; then
          echo "静态产物含 localhost:8000" >&2
          exit 1
        fi
        tar -C dist -cf - . | tar -C /out -xf -'
  then
    rm -rf "$STATIC_STAGE"
    STATIC_STAGE=
    die "前端构建失败，未改动 /var/www/zhengaihe/crawler"
  fi
  py checks.py install-static --src "$STATIC_STAGE" --dst "$static"
  rm -rf "$STATIC_STAGE"
  STATIC_STAGE=
}

stop_app() {
  run_compose 90 stop -t 20 api worker scheduler
}

save_previous_app() {
  APP_ID_FILE="$ROOT/jobs/state/previous-app.ids"
  mkdir -p "$ROOT/jobs/state"
  chmod 700 "$ROOT/jobs/state"
  docker ps -a \
    --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter "label=com.docker.compose.oneoff=False" \
    --format '{{.Label "com.docker.compose.service"}} {{.ID}} {{.Image}}' \
    | awk '$1 == "api" || $1 == "worker" || $1 == "scheduler" { print }' \
    > "$APP_ID_FILE"
  chmod 600 "$APP_ID_FILE"
}

restore_previous_app() {
  local svc id image
  if [[ ! -s "${APP_ID_FILE:-}" ]]; then
    say "没有停止前的 api/worker/scheduler 容器记录。不会新建容器。"
    return 0
  fi
  while read -r svc id image; do
    [[ -n "${id:-}" ]] || continue
    if docker start "$id"; then
      say "已启动停止前的容器 ${svc} ${id}"
    else
      say "原容器 ${svc} ${id} 镜像 ${image} 未能启动。不要 compose up，不要 prune。"
    fi
  done < "$APP_ID_FILE"
}

smoke_new_image() {
  say "停止旧服务之前，用刚构建的镜像和当前代码目录检查导入"
  if ! run_compose 180 run --rm --no-deps --no-build --entrypoint python3 api -c \
    'import sys,PIL,app.main; print("executable="+sys.executable); print("PIL="+PIL.__file__); print("APP_IMPORT_OK")'
  then
    return 1
  fi
}

if [[ "$MODE" == "preflight" ]]; then
  preflight
  exit 0
fi

preflight

if [[ "$UPDATE" == "1" ]]; then
  save_previous_app
  prepare_stage "$REF" "$SHA"
  stop_app
  apply_stage
fi

if [[ "$MODE" == "deploy" && "$SKIP_BUILD" != "1" ]]; then
  if ! run_compose 900 build api worker scheduler; then
    if [[ "$UPDATE" == "1" ]]; then
      restore_previous_app
    fi
    die "构建失败。未删除数据库卷。构建使用 api/Dockerfile.deploy（Debian 12 bookworm）。"
  fi
fi

if [[ "$MODE" == "deploy" ]]; then
  if ! smoke_new_image; then
    if [[ "$UPDATE" == "1" ]]; then
      restore_previous_app
    fi
    die "新镜像导入检查失败，未继续迁移。普通部署此时还没停止旧服务。不要 compose up，不要 prune。"
  fi
  if [[ "$UPDATE" != "1" ]]; then
    save_previous_app
    stop_app
  fi
  if [[ "$SKIP_MIGRATE" != "1" ]]; then
    if ! run_compose 600 run --rm --no-deps --no-build --entrypoint python3 api scripts/migrate_v2.py; then
      restore_previous_app
      die "迁移失败。已尝试启动停止前记录的容器。数据库卷未删除。"
    fi
  fi
  run_compose 180 up -d --no-build --no-recreate --no-deps db redis
  run_compose 180 up -d --no-build --force-recreate --no-deps api
  wait_api
  run_compose 180 up -d --no-build --force-recreate --no-deps worker scheduler
  service_id worker >/dev/null
  service_id scheduler >/dev/null
  service_id db >/dev/null
  service_id redis >/dev/null
  assert_db_redis_unchanged
  if [[ "$SKIP_STATIC" != "1" ]]; then
    build_static
  fi
  say "部署完成。修订: ${RESOLVED_SHA:-见 jobs/state/REVISION}"
  exit 0
fi

run_compose 180 up -d --no-build --no-recreate --no-deps db redis api worker scheduler
wait_api
assert_db_redis_unchanged
say "已按现有镜像启动，未重建 db/redis。"
