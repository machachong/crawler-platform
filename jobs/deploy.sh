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
ARCHIVE=
SOURCE_SWAPPED=0
STATIC_SWAPPED=0
APP_STOPPED=0
APP_IMAGE=
BACKUP=
LOG_FILE=

usage() {
  cat <<'EOF'
用法:
  jobs/deploy.sh [--sha 40位SHA] [--archive 主分支归档.tar.gz]
  jobs/deploy.sh --preflight-only
  jobs/deploy.sh --status
不带 --archive 时解析 GitHub main。--archive 必须同时给出 --sha。
只使用 jobs/state/compose.runtime.yml。不重建 db/redis，不删除卷，不清理镜像，不改 Nginx。
迁移失败不会自动恢复数据库。
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
    --archive) ARCHIVE=${2:-}; shift ;;
    --status) MODE=status ;;
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

if [[ -z "${PYTHON_BASE_IMAGE:-}" ]]; then
  export PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim-bookworm
fi
if [[ -z "${NODE_BASE_IMAGE:-}" ]]; then
  export NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:22-bookworm-slim
fi
if [[ "$MODE" != "status" ]]; then
  require_cmd flock
  acquire_lock
  LOG_FILE="$ROOT/jobs/state/logs/deploy-$(date -u +%Y%m%dT%H%M%SZ).log"
  mkdir -p "$ROOT/jobs/state/logs"
  chmod 700 "$ROOT/jobs/state" "$ROOT/jobs/state/logs"
  touch "$LOG_FILE"
  chmod 600 "$LOG_FILE"
fi
on_exit() {
  local code=$?
  cleanup_temps || true
  if [[ -n "${LOG_FILE:-}" ]]; then
    printf 'exit=%s\n' "$code" >> "$LOG_FILE"
  fi
  return 0
}
trap on_exit EXIT

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

stage_main_source() {
  if [[ -n "$ARCHIVE" ]]; then
    [[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "--archive 必须同时给出 40 位 --sha"
    [[ -f "$ARCHIVE" ]] || die "找不到归档 $ARCHIVE"
    STAGE_DIR=$(mktemp -d)
    chmod 700 "$STAGE_DIR"
    tar -xzf "$ARCHIVE" -C "$STAGE_DIR"
    if [[ -d "$STAGE_DIR/crawler-platform/api" ]]; then
      SOURCE_ROOT="$STAGE_DIR/crawler-platform"
    else
      SOURCE_ROOT="$STAGE_DIR"
    fi
  else
    prepare_stage "$REF" "$SHA"
    SOURCE_ROOT="$STAGE_DIR"
    SHA=$RESOLVED_SHA
  fi
  [[ -f "$SOURCE_ROOT/api/app/captcha.py" ]] || die "源码不像 main：缺少 api/app/captcha.py"
  [[ -f "$SOURCE_ROOT/api/requirements.txt" ]] || die "源码不像 main：缺少 api/requirements.txt"
  [[ -f "$SOURCE_ROOT/api/scripts/migrate_v2.py" ]] || die "源码不像 main：缺少迁移脚本"
}

build_app_image() {
  local ctx pybase
  ctx=$(mktemp -d)
  chmod 700 "$ctx"
  tar -C "$SOURCE_ROOT/api" -cf - . | tar -C "$ctx" -xf -
  mkdir -p "$ctx/deploy"
  cp "$ROOT/api/deploy/mirrors.py" "$ctx/deploy/mirrors.py"
  cp "$ROOT/api/Dockerfile.deploy" "$ctx/Dockerfile.deploy"
  pybase=$(py checks.py base-image --kind python)
  say "构建应用镜像，不使用 --pull 或 --no-cache"
  if ! docker build --pull=false \
      -f "$ctx/Dockerfile.deploy" \
      --build-arg "PYTHON_BASE_IMAGE=${pybase}" \
      --build-arg "APT_MIRROR=${APT_MIRROR:-}" \
      --build-arg "APT_SECURITY_MIRROR=${APT_SECURITY_MIRROR:-}" \
      --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL:-}" \
      -t "crawler-main:${SHA}" \
      "$ctx"; then
    rm -rf "$ctx"
    return 1
  fi
  rm -rf "$ctx"
  APP_IMAGE=$(docker image inspect "crawler-main:${SHA}" --format '{{.Id}}')
  [[ "$APP_IMAGE" =~ ^sha256:[0-9a-f]{64}$ ]] || die "构建结果不是 sha256 镜像 ID"
  say "app_image=${APP_IMAGE}"
}

pin_app_image() {
  "$PY" - "$ROOT/jobs/state/contract.json" "$APP_IMAGE" <<'PY'
import json, sys
path, image = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    contract = json.load(handle)
contract["app_image"] = image
with open(path, "w", encoding="utf-8") as handle:
    json.dump(contract, handle)
PY
  chmod 600 "$ROOT/jobs/state/contract.json"
  py checks.py write-runtime --contract "$ROOT/jobs/state/contract.json" --output "$ROOT/jobs/state/compose.runtime.yml"
  chmod 600 "$ROOT/jobs/state/compose.runtime.yml"
}

smoke_new_image() {
  say "停止旧服务之前，用新镜像 ID 和目标源码检查导入"
  docker run --rm --pull=false --entrypoint python3 "$APP_IMAGE" \
    -v "$SOURCE_ROOT/api:/app:ro" -w /app \
    -c 'import sys,PIL,markdown_it,app.main; print("executable="+sys.executable); print("PIL="+PIL.__file__); print("APP_IMPORT_OK")'
}

backup_private() {
  local static dump db_container
  BACKUP="$ROOT/jobs/state/backups/deploy-${SHA}-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$BACKUP"
  chmod 700 "$BACKUP"
  tar -C "$ROOT" -czf "$BACKUP/api-before.tar.gz" api
  [[ -s "$BACKUP/api-before.tar.gz" ]] || die "api 备份是空的"
  if [[ "${CRAWLER_TESTING:-}" == "1" ]]; then
    static=$CRAWLER_STATIC_ROOT
  else
    static=/var/www/zhengaihe/crawler
  fi
  tar -C "$(dirname "$static")" -czf "$BACKUP/static-before.tar.gz" "$(basename "$static")"
  [[ -s "$BACKUP/static-before.tar.gz" ]] || die "静态备份是空的"
  cp "$ROOT/docker-compose.override.yml" "$BACKUP/docker-compose.override.yml"
  cp "$ROOT/jobs/state/compose.runtime.yml" "$BACKUP/compose.runtime.yml"
  if [[ -f "$ROOT/.env" ]]; then
    cp "$ROOT/.env" "$BACKUP/env"
    chmod 600 "$BACKUP/env"
  fi
  chmod 600 "$BACKUP"/*
  db_container=$(py checks.py print-field --contract "$ROOT/jobs/state/contract.json" --field db_container)
  dump="$BACKUP/db.dump"
  docker exec "$db_container" pg_dump -U user -d crawler_db -Fc > "$dump"
  chmod 600 "$dump"
  [[ "$(head -c 5 "$dump")" == "PGDMP" ]] || die "数据库备份不是 PostgreSQL 自定义格式"
  say "备份目录: $BACKUP"
  say "数据库备份已校验为 PGDMP。失败时不会自动恢复数据库。"
}

swap_source() {
  tar -C "$SOURCE_ROOT/api" -cf - . | tar -C "$ROOT/api" -xf -
  mkdir -p "$ROOT/web"
  tar -C "$SOURCE_ROOT/web" -cf - . | tar -C "$ROOT/web" -xf -
  SOURCE_SWAPPED=1
}

rollback_tree() {
  local static
  if [[ "$SOURCE_SWAPPED" == "1" && -f "$BACKUP/api-before.tar.gz" ]]; then
    tar -C "$ROOT" -xzf "$BACKUP/api-before.tar.gz"
    if [[ -d "$SOURCE_ROOT/web" && -f "$BACKUP/static-before.tar.gz" ]]; then
      :
    fi
    say "已恢复切换前的 api 目录"
  fi
  if [[ "${CRAWLER_TESTING:-}" == "1" ]]; then
    static=$CRAWLER_STATIC_ROOT
  else
    static=/var/www/zhengaihe/crawler
  fi
  if [[ "$STATIC_SWAPPED" == "1" && -f "$BACKUP/static-before.tar.gz" ]]; then
    tar -C "$(dirname "$static")" -xzf "$BACKUP/static-before.tar.gz"
    say "已恢复切换前的静态目录"
  fi
  if [[ "$APP_STOPPED" == "1" ]]; then
    docker rm -f crawler-main-api crawler-main-worker crawler-main-scheduler >/dev/null 2>&1 || true
    restore_previous_app
  fi
}

fail_deploy() {
  rollback_tree
  die "$1"
}

write_release() {
  local path="$ROOT/jobs/state/RELEASE"
  umask 077
  cat > "$path" <<EOF
sha=$SHA
app_image=$APP_IMAGE
db_image=$(py checks.py print-field --contract "$ROOT/jobs/state/contract.json" --field db_image)
redis_image=$(py checks.py print-field --contract "$ROOT/jobs/state/contract.json" --field redis_image)
db_container=$(py checks.py print-field --contract "$ROOT/jobs/state/contract.json" --field db_container)
redis_container=$(py checks.py print-field --contract "$ROOT/jobs/state/contract.json" --field redis_container)
redis_volume=$(py checks.py print-field --contract "$ROOT/jobs/state/contract.json" --field redis_volume)
EOF
  chmod 600 "$path"
  say "sha=$SHA"
  say "app_image=$APP_IMAGE"
}

if [[ "$MODE" == "status" ]]; then
  if [[ -f "$ROOT/jobs/state/RELEASE" ]]; then
    say "最近成功标记:"
    sed -n '1,12p' "$ROOT/jobs/state/RELEASE"
  else
    say "还没有成功部署标记"
  fi
  exit 0
fi

if [[ "$MODE" == "preflight" ]]; then
  preflight
  exit 0
fi

preflight
stage_main_source
if [[ "$SKIP_BUILD" != "1" ]]; then
  build_app_image || die "构建失败。未停止旧服务，未删除数据库卷。"
else
  die "正式部署不能跳过构建。"
fi
pin_app_image
if ! smoke_new_image; then
  die "新镜像导入检查失败。未停止旧服务，未切换源码，未迁移。日志: ${LOG_FILE}"
fi
backup_private || die "备份失败。未停止旧服务。"
save_previous_app
swap_source
stop_app
APP_STOPPED=1
NET=$(py checks.py print-field --contract "$ROOT/jobs/state/contract.json" --field network)
if [[ "$SKIP_MIGRATE" != "1" ]]; then
  if ! docker run --rm --pull=false --network "$NET" --entrypoint python3 "$APP_IMAGE" \
      -v "$ROOT/api:/app:ro" -w /app \
      -e DATABASE_URL=postgresql://user:pass@db:5432/crawler_db \
      -e REDIS_URL=redis://redis:6379/0 \
      scripts/migrate_v2.py; then
    fail_deploy "迁移失败。已尝试恢复源码和停止前的容器。数据库备份不会自动恢复。日志: ${LOG_FILE}"
  fi
fi
run_compose 180 up -d --no-build --no-recreate --no-deps db redis
ENV_FILE="$BACKUP/api.env"
api_id=$(awk '$1 == "api" { print $2; exit }' "$APP_ID_FILE")
docker inspect "$api_id" --format '{{range .Config.Env}}{{println .}}{{end}}' > "$ENV_FILE"
chmod 600 "$ENV_FILE"
start_named() {
  local name="$1"; shift
  docker rm -f "$name" >/dev/null 2>&1 || true
  docker run -d --name "$name" --pull=false --network "$NET" \
    --label "com.docker.compose.project=${PROJECT}" \
    --label "com.docker.compose.oneoff=False" \
    --env-file "$ENV_FILE" \
    -v "$ROOT/api:/app" -w /app \
    "$@"
}
if ! start_named crawler-main-api -p 8010:8000 --label com.docker.compose.service=api \
    --entrypoint uvicorn "$APP_IMAGE" app.main:app --host 0.0.0.0 --port 8000; then
  fail_deploy "新 API 容器没有用目标镜像启动。日志: ${LOG_FILE}"
fi
if ! wait_api; then
  fail_deploy "新 API 未通过健康检查。已尝试退回旧容器和旧源码。数据库备份不会自动恢复。日志: ${LOG_FILE}"
fi
start_named crawler-main-worker --label com.docker.compose.service=worker \
  --entrypoint celery "$APP_IMAGE" -A app.worker.tasks worker --loglevel=info --concurrency=2 \
  || fail_deploy "worker 没有用目标镜像启动。日志: ${LOG_FILE}"
start_named crawler-main-scheduler --restart unless-stopped --label com.docker.compose.service=scheduler \
  --entrypoint python3 "$APP_IMAGE" scripts/scheduler.py \
  || fail_deploy "scheduler 没有用目标镜像启动。日志: ${LOG_FILE}"
running_image=$(docker inspect crawler-main-api --format '{{.Image}}')
[[ "$running_image" == "$APP_IMAGE" ]] || fail_deploy "运行中的 API 不是目标镜像 ${APP_IMAGE}"
service_id db >/dev/null
service_id redis >/dev/null
assert_db_redis_unchanged
if [[ "$SKIP_STATIC" != "1" ]]; then
  STATIC_SWAPPED=1
  build_static || fail_deploy "静态发布失败。已尝试恢复旧静态目录和旧容器。日志: ${LOG_FILE}"
fi
write_release
say "部署完成。退出码将记为 0。日志: ${LOG_FILE}"
exit 0
