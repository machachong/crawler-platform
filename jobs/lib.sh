#!/bin/bash
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "请通过 jobs/deploy.sh 或 jobs/update-from-github.sh 运行" >&2
  exit 1
fi

set -euo pipefail

JOBS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ -n "${CRAWLER_ROOT:-}" ]]; then
  ROOT=$(cd "$CRAWLER_ROOT" && pwd)
else
  ROOT=$(cd "$JOBS_DIR/.." && pwd)
fi
cd "$ROOT"

case "$ROOT" in
  /|/home|/var|/var/www|/var/www/zhengaihe|/etc|/etc/nginx)
    echo "错误: 拒绝把 ${ROOT} 当作项目目录" >&2
    exit 1
    ;;
esac
if [[ "${CRAWLER_TESTING:-}" == "1" && "$ROOT" == "/home/workspace/crawler-platform" ]]; then
  echo "错误: 禁止在生产目录启用 CRAWLER_TESTING" >&2
  exit 1
fi

REPO_URL=${CRAWLER_REPO:-https://github.com/machachong/crawler-platform.git}
if [[ "${CRAWLER_TESTING:-}" == "1" ]]; then
  MIN_FREE_MB=0
else
  MIN_FREE_MB=${CRAWLER_MIN_FREE_MB:-3072}
fi
EXPECT_PROJECT=${CRAWLER_EXPECT_PROJECT:-crawler-platform}
EXPECT_VOLUME=${CRAWLER_EXPECT_VOLUME:-crawler-platform_postgres_data}
PROJECT=
RESOLVED_SHA=
RESOLVED_REF=
GIT_TMP=
STAGE_DIR=
INSPECT_FILE=
CONFIG_FILE=
EMPTY_GITCONFIG=
STATIC_STAGE=

die() { printf '错误: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"; }

python_is_new_enough() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

choose_python() {
  local candidate
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if ! python_is_new_enough "$PYTHON_BIN"; then
      die "PYTHON_BIN=${PYTHON_BIN} 不可用或低于 Python 3.9。不会修改系统默认 python3。"
    fi
    PY=$PYTHON_BIN
    return 0
  fi
  for candidate in python3.11 python3.10 python3.9 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && python_is_new_enough "$candidate"; then
      PY=$candidate
      return 0
    fi
  done
  die "未找到 Python 3.9 或更新的解释器。可设置 PYTHON_BIN=/usr/bin/python3.11。不会修改系统默认 python3，也不要改 PATH。"
}

choose_python
py() { "$PY" "$JOBS_DIR/py/$1" "${@:2}"; }

acquire_lock() {
  mkdir -p "$ROOT/jobs/state"
  chmod 700 "$ROOT/jobs/state"
  exec 9>"$ROOT/jobs/state/crawler.lock"
  flock -n 9 || die "已有更新或部署正在运行"
}

cleanup_temps() {
  [[ -n "${GIT_TMP:-}" ]] && rm -rf "$GIT_TMP"
  [[ -n "${STAGE_DIR:-}" ]] && rm -rf "$STAGE_DIR"
  [[ -n "${INSPECT_FILE:-}" ]] && rm -f "$INSPECT_FILE"
  [[ -n "${CONFIG_FILE:-}" ]] && rm -f "$CONFIG_FILE"
  [[ -n "${EMPTY_GITCONFIG:-}" ]] && rm -f "$EMPTY_GITCONFIG"
  [[ -n "${STATIC_STAGE:-}" ]] && rm -rf "$STATIC_STAGE"
  return 0
}

git_cmd() {
  local limit="$1"
  shift
  timeout "$limit" git \
    -c "safe.directory=*" \
    -c credential.helper= \
    -c core.askPass=/bin/false \
    -c http.lowSpeedLimit=1000 \
    -c http.lowSpeedTime=30 \
    "$@"
}

retry_git() {
  local attempts="$1" limit="$2" n=1
  shift 2
  while true; do
    if git_cmd "$limit" "$@"; then
      return 0
    fi
    if (( n >= attempts )); then
      return 1
    fi
    n=$((n + 1))
    sleep $((n * 2))
  done
}

load_mirrors() {
  local exports
  exports=$( "$PY" "$JOBS_DIR/../api/deploy/mirrors.py" shell-exports "$ROOT/jobs/mirrors.env")
  if [[ -n "$exports" ]]; then
    # shellcheck disable=SC2086
    eval "$exports"
  fi
}

collect_inspect() {
  local out="$1" id
  local ids=()
  while IFS= read -r id; do
    [[ -n "$id" ]] && ids+=("$id")
  done < <(docker ps -aq --filter "label=com.docker.compose.project.working_dir=${ROOT}")
  if ((${#ids[@]} == 0)); then
    printf '[]\n' > "$out"
    return 0
  fi
  docker inspect "${ids[@]}" > "$out"
}

run_compose() {
  local limit="$1"
  shift
  [[ -n "${PROJECT:-}" ]] || die "内部错误: 未设置项目名"
  [[ -f "$ROOT/jobs/state/compose.runtime.yml" ]] || die "缺少 jobs/state/compose.runtime.yml"
  timeout "$limit" env -u COMPOSE_FILE -u COMPOSE_PROJECT_NAME -u COMPOSE_PATH_SEPARATOR \
    docker compose \
      --project-directory "$ROOT" \
      --project-name "$PROJECT" \
      -f "$ROOT/jobs/state/compose.runtime.yml" \
      "$@"
}

app_is_running() {
  local rc
  set +e
  py checks.py app-running --inspect "$1"
  rc=$?
  set -e
  if [[ "$rc" == "0" ]]; then
    return 0
  fi
  if [[ "$rc" == "1" ]]; then
    return 1
  fi
  die "无法判断 api 是否在运行"
}

prepare_stage() {
  local ref="$1" sha="$2" out count got
  py checks.py assert-repo --url "$REPO_URL" >/dev/null
  EMPTY_GITCONFIG=$(mktemp)
  export GIT_CONFIG_GLOBAL="$EMPTY_GITCONFIG"
  export GIT_CONFIG_NOSYSTEM=1
  export GIT_TERMINAL_PROMPT=0
  if [[ -n "$sha" ]]; then
    [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || die "SHA 必须是 40 位小写十六进制"
    RESOLVED_SHA=$sha
    RESOLVED_REF=sha
  else
    [[ "$ref" =~ ^[A-Za-z0-9._/-]{1,80}$ ]] || die "ref 非法"
    [[ "$ref" != *..* && "$ref" != /* ]] || die "ref 非法"
    if ! out=$(retry_git 3 60 ls-remote "$REPO_URL" "refs/heads/${ref}"); then
      die "无法读取 GitHub 引用 ${ref}"
    fi
    count=$(printf '%s\n' "$out" | sed '/^$/d' | wc -l | tr -d ' ')
    [[ "$count" == "1" ]] || die "引用 refs/heads/${ref} 没有唯一提交"
    RESOLVED_SHA=$(printf '%s\n' "$out" | awk 'NR==1 { print $1 }')
    [[ "$RESOLVED_SHA" =~ ^[0-9a-f]{40}$ ]] || die "ls-remote 返回的不是提交 SHA"
    RESOLVED_REF="refs/heads/${ref}"
  fi
  GIT_TMP=$(mktemp -d)
  chmod 700 "$GIT_TMP"
  git_cmd 30 init -q "$GIT_TMP"
  if ! retry_git 3 180 -C "$GIT_TMP" fetch --depth 1 "$REPO_URL" "$RESOLVED_SHA"; then
    die "拉取 ${RESOLVED_SHA} 失败"
  fi
  got=$(git_cmd 30 -C "$GIT_TMP" rev-parse FETCH_HEAD)
  [[ "$got" == "$RESOLVED_SHA" ]] || die "拉取结果与指定修订不一致"
  STAGE_DIR=$(mktemp -d)
  chmod 700 "$STAGE_DIR"
  git_cmd 60 -C "$GIT_TMP" archive --format=tar "$RESOLVED_SHA" | tar -x -C "$STAGE_DIR"
  py tree_sync.py assert-stage --stage "$STAGE_DIR"
}

apply_stage() {
  local stamp backup
  mkdir -p "$ROOT/jobs/state/backups"
  chmod 700 "$ROOT/jobs/state" "$ROOT/jobs/state/backups"
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  backup="$ROOT/jobs/state/backups/code-${stamp}-${RESOLVED_SHA}.tar.gz"
  py tree_sync.py backup --root "$ROOT" --output "$backup"
  py tree_sync.py sync --stage "$STAGE_DIR" --root "$ROOT" --manifest "$ROOT/jobs/state/upstream-manifest.txt"
  py tree_sync.py revision --path "$ROOT/jobs/state/REVISION" --sha "$RESOLVED_SHA" --ref "$RESOLVED_REF" --source "$REPO_URL"
  py tree_sync.py prune --dir "$ROOT/jobs/state/backups" --keep 2
  say "已固定修订: ${RESOLVED_SHA}"
  say "来源: ${REPO_URL} ${RESOLVED_REF}"
}

preflight() {
  local ver
  require_cmd docker
  require_cmd timeout
  say "Python: ${PY}"
  [[ -f "$ROOT/docker-compose.override.yml" ]] || die "缺少 docker-compose.override.yml。不要删除现有覆盖文件。部署不会加载它，避免把 5432/6379/8000 合并进来。"
  [[ -f "$ROOT/api/Dockerfile.deploy" ]] || die "缺少 api/Dockerfile.deploy"
  [[ -f "$ROOT/api/scripts/migrate_v2.py" ]] || die "缺少 api/scripts/migrate_v2.py"
  if ! docker compose version >/dev/null 2>&1; then
    die "docker compose 插件不可用。不会安装或升级 Docker。请把 docker compose version 的输出发回。"
  fi
  ver=$(docker compose version --short 2>/dev/null || docker compose version 2>/dev/null || true)
  py checks.py compose-version --text "$ver"
  load_mirrors
  say "APT_MIRROR: ${APT_MIRROR:-未设置，使用 deb.debian.org}"
  say "APT_SECURITY_MIRROR: ${APT_SECURITY_MIRROR:-未设置，使用 deb.debian.org}"
  say "PIP_INDEX_URL: ${PIP_INDEX_URL:-未设置，使用 pypi.org}"
  say "NPM_REGISTRY: ${NPM_REGISTRY:-未设置，使用 registry.npmjs.org}"
  py checks.py disk --min-mb "$MIN_FREE_MB" --path "$ROOT" --path /var/lib/docker --path /var/www/zhengaihe
  INSPECT_FILE=$(mktemp)
  chmod 600 "$INSPECT_FILE"
  collect_inspect "$INSPECT_FILE"
  mkdir -p "$ROOT/jobs/state"
  chmod 700 "$ROOT/jobs/state"
  py checks.py discover \
    --root "$ROOT" \
    --inspect "$INSPECT_FILE" \
    --expect-project "$EXPECT_PROJECT" \
    --expect-volume "$EXPECT_VOLUME" \
    > "$ROOT/jobs/state/contract.json"
  chmod 600 "$ROOT/jobs/state/contract.json"
  rm -f "$INSPECT_FILE"
  INSPECT_FILE=
  py checks.py write-runtime \
    --contract "$ROOT/jobs/state/contract.json" \
    --output "$ROOT/jobs/state/compose.runtime.yml"
  chmod 600 "$ROOT/jobs/state/compose.runtime.yml"
  PROJECT=$(py checks.py print-field --contract "$ROOT/jobs/state/contract.json" --field project)
  CONFIG_FILE=$(mktemp)
  chmod 600 "$CONFIG_FILE"
  run_compose 120 config > "$CONFIG_FILE"
  py checks.py check-rendered --contract "$ROOT/jobs/state/contract.json" --config "$CONFIG_FILE"
  rm -f "$CONFIG_FILE"
  CONFIG_FILE=
  py checks.py print-contract --contract "$ROOT/jobs/state/contract.json"
  if [[ "${CRAWLER_TESTING:-}" != "1" && ! -d /var/www/zhengaihe/crawler ]]; then
    die "静态目录不存在: /var/www/zhengaihe/crawler"
  fi
}
