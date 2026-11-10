#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
REPO=$(pwd)

bash -n jobs/lib.sh
bash -n jobs/deploy.sh
bash -n jobs/update-from-github.sh
bash -n jobs/start-backend.sh

for file in jobs/lib.sh jobs/deploy.sh jobs/update-from-github.sh jobs/start-backend.sh api/Dockerfile.deploy; do
  if grep -n -E 'git reset|git clean|rsync|--fix-missing|Check-Valid-Until|trusted=yes|compose down|volume prune|system prune|ghfast|!override' "$file"; then
    echo "forbidden pattern in $file" >&2
    exit 1
  fi
done
if grep -n -E -- '-f[[:space:]]+.*docker-compose\.yml' jobs/deploy.sh jobs/lib.sh; then
  echo "deploy still stacks the stock compose file" >&2
  exit 1
fi

python3 jobs/tests/test_guards.py

PY_STUB=$(mktemp -d)
cat > "$PY_STUB/python3" <<'EOF'
#!/bin/bash
exit 1
EOF
chmod +x "$PY_STUB/python3"
for pyname in python3.9 python3.10 python3.11; do
  cp "$PY_STUB/python3" "$PY_STUB/$pyname"
done
set +e
PY_OUT=$(PATH="$PY_STUB:/usr/bin:/bin" jobs/deploy.sh --help 2>&1)
PY_RC=$?
set -e
[[ "$PY_RC" -ne 0 ]]
grep -q 'PYTHON_BIN' <<<"$PY_OUT"
PY_OK=$(PATH="$PY_STUB:/usr/bin:/bin" PYTHON_BIN="$(command -v python3)" jobs/deploy.sh --help 2>&1)
grep -q '用法' <<<"$PY_OK"
rm -rf "$PY_STUB"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
export CRAWLER_TESTING=1
export GIT_CONFIG_GLOBAL="$TMP/empty-gitconfig"
touch "$GIT_CONFIG_GLOBAL"

WORK="$TMP/work"
BARE="$TMP/origin.git"
SERVER="$TMP/server"
mkdir -p "$WORK/api/scripts" "$WORK/jobs" "$SERVER/jobs" "$SERVER/api/app"
printf 'from python:3.10-slim\n' > "$WORK/api/Dockerfile"
printf 'services: {}\n' > "$WORK/docker-compose.yml"
printf 'print("migrate")\n' > "$WORK/api/scripts/migrate_v2.py"
printf 'echo deploy-v1\n' > "$WORK/jobs/deploy.sh"
chmod +x "$WORK/jobs/deploy.sh"
printf 'remove-me\n' > "$WORK/removed-later.txt"
printf 'readme-v1\n' > "$WORK/README.md"
git -C "$WORK" init -q -b main
git -C "$WORK" config user.email test@example.com
git -C "$WORK" config user.name test
git -C "$WORK" add .
git -C "$WORK" commit -q -m init
git clone -q --bare "$WORK" "$BARE"
SHA1=$(git -C "$WORK" rev-parse HEAD)

SECRET=sk-test-SECRET-do-not-leak
printf 'OPENAI_API_KEY=%s\n' "$SECRET" > "$SERVER/.env"
printf 'PORTS-KEEP\n' > "$SERVER/docker-compose.override.yml"
printf 'ADMIN-KEEP\n' > "$SERVER/jobs/create-admin.sh"
printf 'CUSTOM-KEEP\n' > "$SERVER/jobs/custom-server.sh"
printf 'old-main\n' > "$SERVER/api/app/main.py"
printf 'old-readme\n' > "$SERVER/README.md"

BIN="$TMP/bin"
mkdir -p "$BIN"
export DOCKER_LOG="$TMP/docker.log"
touch "$DOCKER_LOG"
cat > "$BIN/docker" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$DOCKER_LOG"
mode=${DOCKER_MODE:-empty}
if [[ "$1" == "ps" ]]; then
  if [[ "$mode" == "preflight" ]]; then
    printf '%s\n' db111 redis111 api111 worker111 sch111
  elif [[ "$mode" == "running" ]]; then
    printf '%s\n' api111
  fi
  exit 0
fi
if [[ "$1" == "inspect" ]]; then
  cat "$FIXTURE_INSPECT"
  exit 0
fi
if [[ "$1" == "compose" ]]; then
  if [[ "$*" == *"--short"* ]]; then
    printf '%s\n' "2.2.1"
    exit 0
  fi
  if [[ "$*" == *"version"* ]]; then
    printf '%s\n' "Docker Compose version v2.2.1"
    exit 0
  fi
  if [[ "$*" == *"config"* ]]; then
    cat "$FIXTURE_CONFIG"
    exit 0
  fi
fi
echo "unexpected docker: $*" >&2
exit 99
EOF
chmod +x "$BIN/docker"
export PATH="$BIN:$PATH"

export DOCKER_MODE=running
export FIXTURE_INSPECT="$TMP/running.json"
cat > "$FIXTURE_INSPECT" <<'EOF'
[{"Config":{"Labels":{"com.docker.compose.service":"api","com.docker.compose.oneoff":"False"}},"State":{"Status":"running"}}]
EOF
set +e
RUN_OUT=$(CRAWLER_ROOT="$SERVER" CRAWLER_REPO="$BARE" jobs/update-from-github.sh --ref main 2>&1)
RUN_RC=$?
set -e
[[ "$RUN_RC" -ne 0 ]]
grep -q 'deploy.sh --update' <<<"$RUN_OUT"
[[ "$(cat "$SERVER/api/app/main.py")" == "old-main" ]]
[[ ! -f "$SERVER/jobs/state/REVISION" ]]

export DOCKER_MODE=empty
unset FIXTURE_INSPECT || true
OUT=$(CRAWLER_ROOT="$SERVER" CRAWLER_REPO="$BARE" jobs/update-from-github.sh --sha "$SHA1")
grep -q "$SHA1" <<<"$OUT"
grep -q '已固定修订' <<<"$OUT"
if grep -q "$SECRET" <<<"$OUT"; then
  echo "update output contains the secret" >&2
  exit 1
fi
[[ "$(cat "$SERVER/README.md")" == "readme-v1" ]]
[[ "$(cat "$SERVER/.env")" == "OPENAI_API_KEY=${SECRET}" ]]
[[ "$(cat "$SERVER/docker-compose.override.yml")" == "PORTS-KEEP" ]]
[[ "$(cat "$SERVER/jobs/create-admin.sh")" == "ADMIN-KEEP" ]]
[[ "$(cat "$SERVER/jobs/custom-server.sh")" == "CUSTOM-KEEP" ]]
[[ "$(cat "$SERVER/api/app/main.py")" == "old-main" ]]
[[ ! -d "$SERVER/.git" ]]
grep -q "^sha=${SHA1}$" "$SERVER/jobs/state/REVISION"
if grep -q "$SECRET" "$SERVER/jobs/state/REVISION"; then
  echo "revision file contains the secret" >&2
  exit 1
fi
python3 - "$SERVER/jobs/state/backups" "$SECRET" <<'PY'
import sys, tarfile
from pathlib import Path
secret = sys.argv[2].encode()
backups = list(Path(sys.argv[1]).glob("code-*.tar.gz"))
assert backups
for path in backups:
    assert secret not in path.read_bytes()
    with tarfile.open(path, "r:gz") as archive:
        assert all(name != ".env" and not name.endswith("/.env") for name in archive.getnames())
PY

printf 'readme-v2\n' > "$WORK/README.md"
rm "$WORK/removed-later.txt"
printf 'echo deploy-v2\n' > "$WORK/jobs/deploy.sh"
git -C "$WORK" add -A
git -C "$WORK" commit -q -m second
git -C "$WORK" push -q "$BARE" HEAD:main
SHA2=$(git -C "$WORK" rev-parse HEAD)
CRAWLER_ROOT="$SERVER" CRAWLER_REPO="$BARE" jobs/update-from-github.sh --ref main >/dev/null
[[ "$(cat "$SERVER/README.md")" == "readme-v2" ]]
[[ ! -f "$SERVER/removed-later.txt" ]]
[[ "$(cat "$SERVER/jobs/custom-server.sh")" == "CUSTOM-KEEP" ]]
[[ "$(cat "$SERVER/jobs/create-admin.sh")" == "ADMIN-KEEP" ]]
grep -q "^sha=${SHA2}$" "$SERVER/jobs/state/REVISION"
COUNT=$(find "$SERVER/jobs/state/backups" -name 'code-*.tar.gz' | wc -l | tr -d ' ')
[[ "$COUNT" == "2" ]]

set +e
BAD=$(CRAWLER_ROOT="$SERVER" CRAWLER_REPO='https://ghfast.top/github.com/machachong/crawler-platform.git' jobs/update-from-github.sh --ref main 2>&1)
BAD_RC=$?
set -e
[[ "$BAD_RC" -ne 0 ]]
grep -q '不安全\|只允许' <<<"$BAD"

PRE="$TMP/preflight"
mkdir -p "$PRE/api/scripts" "$PRE/jobs"
printf 'override-keep\n' > "$PRE/docker-compose.override.yml"
printf 'print("migrate")\n' > "$PRE/api/scripts/migrate_v2.py"
cp "$REPO/api/Dockerfile.deploy" "$PRE/api/Dockerfile.deploy"
python3 - "$PRE" "$TMP/inspect.json" "$TMP/config.yml" <<'PY'
import json, sys
root, inspect_path, config_path = sys.argv[1:]
secret = "sk-live-secret-do-not-print"

def container(service, ports, volume=False, health=True, image="1"):
    bindings = {}
    for container_port, host_port in ports:
        bindings[f"{container_port}/tcp"] = [{"HostIp": "", "HostPort": str(host_port)}]
    mounts = []
    if volume:
        mounts.append({
            "Type": "volume",
            "Name": "crawler-platform_postgres_data",
            "Destination": "/var/lib/postgresql/data",
        })
    if service == "redis":
        mounts.append({
            "Type": "volume",
            "Name": "e" * 64,
            "Destination": "/data",
        })
    state = {"Status": "running"}
    if health:
        state["Health"] = {"Status": "healthy"}
    return {
        "Id": image * 64,
        "Name": f"/crawler-platform-{service}-1",
        "Image": "sha256:" + image * 64,
        "State": state,
        "HostConfig": {"PortBindings": bindings or None},
        "Mounts": mounts,
        "NetworkSettings": {"Networks": {"crawler-platform_default": {}}},
        "Config": {
            "Image": "docker.m.daocloud.io/library/postgres:15-alpine" if service == "db" else "docker.m.daocloud.io/library/redis:7-alpine",
            "Labels": {
                "com.docker.compose.project": "crawler-platform",
                "com.docker.compose.project.working_dir": root,
                "com.docker.compose.service": service,
                "com.docker.compose.oneoff": "False",
            },
            "Env": [f"OPENAI_API_KEY={secret}"],
        },
    }

containers = [
    container("db", [(5432, 5433)], volume=True, image="a"),
    container("redis", [(6379, 6380)], image="b"),
    container("api", [(8000, 8010)], image="c"),
    container("worker", [], health=False, image="d"),
    container("scheduler", [], health=False, image="e"),
]
open(inspect_path, "w").write(json.dumps(containers))
db_image = "sha256:" + "a" * 64
redis_image = "sha256:" + "b" * 64
redis_volume = "e" * 64
open(config_path, "w").write(f"""
name: crawler-platform
services:
  api:
    ports:
      - mode: ingress
        target: 8000
        published: "8010"
        protocol: tcp
  db:
    image: {db_image}
    pull_policy: never
    ports:
      - mode: ingress
        target: 5432
        published: "5433"
        protocol: tcp
  redis:
    image: {redis_image}
    pull_policy: never
    ports:
      - mode: ingress
        target: 6379
        published: "6380"
        protocol: tcp
  worker: {{}}
  scheduler: {{}}
networks:
  default:
    name: crawler-platform_default
    external: true
volumes:
  postgres_data:
    name: crawler-platform_postgres_data
    external: true
  redis_data:
    name: {redis_volume}
    external: true
""")
PY
export DOCKER_MODE=preflight
export FIXTURE_INSPECT="$TMP/inspect.json"
export FIXTURE_CONFIG="$TMP/config.yml"
: > "$DOCKER_LOG"
PRE_OUT=$(CRAWLER_ROOT="$PRE" jobs/deploy.sh --preflight-only)
grep -q 'Compose 版本: 2.2.1' <<<"$PRE_OUT"
grep -q '不要求 Compose 2.24' <<<"$PRE_OUT"
grep -q '数据库卷: crawler-platform_postgres_data' <<<"$PRE_OUT"
grep -q '8010->8000' <<<"$PRE_OUT"
if grep -q 'sk-live-secret' <<<"$PRE_OUT"; then
  echo "preflight printed a secret" >&2
  exit 1
fi
if grep -q 'sk-live-secret' "$PRE/jobs/state/contract.json"; then
  echo "contract stored a secret" >&2
  exit 1
fi
if grep -q '!override' "$PRE/jobs/state/compose.runtime.yml"; then
  echo "runtime file uses the override tag" >&2
  exit 1
fi
grep -q '8010:8000' "$PRE/jobs/state/compose.runtime.yml"
grep -q '5433:5432' "$PRE/jobs/state/compose.runtime.yml"
grep -q 'external:' "$PRE/jobs/state/compose.runtime.yml"
grep -q 'pull_policy: never' "$PRE/jobs/state/compose.runtime.yml"
grep -q 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' "$PRE/jobs/state/compose.runtime.yml"
if grep -q 'postgres:15-alpine\|redis:7-alpine' "$PRE/jobs/state/compose.runtime.yml"; then
  echo "runtime file selects stock database tags" >&2
  exit 1
fi
if grep -q '5432:5432' "$PRE/jobs/state/compose.runtime.yml"; then
  echo "runtime file publishes the default db port" >&2
  exit 1
fi
grep -q -- '--project-name crawler-platform' "$DOCKER_LOG"
grep -q 'compose.runtime.yml' "$DOCKER_LOG"
if grep -E '(^| )(stop|down|rm)( |$)' "$DOCKER_LOG"; then
  echo "preflight invoked a mutating docker command" >&2
  exit 1
fi
[[ "$(cat "$PRE/docker-compose.override.yml")" == "override-keep" ]]

python3 - "$TMP/config.yml" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
text = text.replace('published: "5433"', 'published: "5433"\n      - mode: ingress\n        target: 5432\n        published: "5432"\n        protocol: tcp', 1)
path.write_text(text)
PY
set +e
BAD_PRE=$(CRAWLER_ROOT="$PRE" jobs/deploy.sh --preflight-only 2>&1)
BAD_PRE_RC=$?
set -e
[[ "$BAD_PRE_RC" -ne 0 ]]
grep -q '5432' <<<"$BAD_PRE"
echo "shell checks ok"
