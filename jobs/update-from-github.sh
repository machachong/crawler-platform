#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"
trap cleanup_temps EXIT

REF=main
SHA=
APPLY_LIVE=0

usage() {
  cat <<'EOF'
用法: jobs/update-from-github.sh [--ref main] [--sha 40位SHA] [--apply-live]
从 https://github.com/machachong/crawler-platform.git 拉取并固定到一个提交。
只使用官方 GitHub，不改写已有提交，也不清理未跟踪文件。保留 .env、
docker-compose.override.yml、jobs/create-admin.sh、jobs/mirrors.env，
以及其他仓库里没有的 jobs 文件。
api 正在运行时默认拒绝写入。请改用 jobs/deploy.sh --update。
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ref) REF=${2:-}; shift ;;
    --sha) SHA=${2:-}; shift ;;
    --apply-live) APPLY_LIVE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
  shift
done

require_cmd git
require_cmd timeout
require_cmd flock
require_cmd docker
acquire_lock
INSPECT_FILE=$(mktemp)
chmod 600 "$INSPECT_FILE"
collect_inspect "$INSPECT_FILE"
if app_is_running "$INSPECT_FILE"; then
  if [[ "$APPLY_LIVE" != "1" ]]; then
    die "api/worker/scheduler 正在运行，并且代码目录已挂进容器。请改跑 jobs/deploy.sh --update。它会先停止这三项再写入，不停止 db/redis。"
  fi
  say "警告: 正在向运行中的代码目录写入"
fi
rm -f "$INSPECT_FILE"
INSPECT_FILE=
py checks.py disk --min-mb "$MIN_FREE_MB" --path "$ROOT" --path /var/lib/docker
prepare_stage "$REF" "$SHA"
apply_stage
