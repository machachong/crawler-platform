#!/usr/bin/env python3
"""Mock checks for deploy guards. No Docker, no production access."""

from __future__ import annotations

import importlib.util
import json
import os
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checks = load("checks", ROOT / "jobs/py/checks.py")
tree = load("tree_sync", ROOT / "jobs/py/tree_sync.py")
mirrors = load("mirrors", ROOT / "api/deploy/mirrors.py")


def fail(message: str) -> None:
    raise SystemExit(message)


def raises(fn) -> BaseException | None:
    try:
        fn()
    except (checks.GuardError, mirrors.MirrorError, tree.SyncError, SystemExit) as exc:
        return exc
    return None


def contract() -> dict:
    return {
        "project": "crawler-platform",
        "working_dir": "/home/workspace/crawler-platform",
        "volume": "crawler-platform_postgres_data",
        "network": "crawler-platform_default",
        "db_container": "a" * 64,
        "redis_container": "b" * 64,
        "db_image": "sha256:" + "c" * 64,
        "redis_image": "sha256:" + "d" * 64,
        "redis_volume": "e" * 64,
        "ports": {
            "api": {"host_ip": "", "host_port": 8010, "container_port": 8000},
            "db": {"host_ip": "", "host_port": 5433, "container_port": 5432},
            "redis": {"host_ip": "", "host_port": 6380, "container_port": 6379},
        },
    }


def test_runtime_file_replaces_ports() -> None:
    text = checks.runtime_yaml(contract())
    if "!override" in text or "5432:5432" in text or "6379:6379" in text or "8000:8000" in text:
        fail("runtime file still merges default host ports")
    for required in ("8010:8000", "5433:5432", "6380:6379", "crawler-platform_postgres_data", "external:", "pull_policy: never", contract()["db_image"], contract()["redis_volume"]):
        if required not in text:
            fail(f"runtime file missing {required}")
    if "postgres:15-alpine" in text or "redis:7-alpine" in text:
        fail("runtime file selects stock database image names")
    if 'PYTHON_BASE_IMAGE: "python:3.11-slim-bookworm"' not in text:
        fail("default python base image changed")
    os.environ["PYTHON_BASE_IMAGE"] = "docker.m.daocloud.io/library/python:3.11-slim-bookworm"
    try:
        mirrored = checks.runtime_yaml(contract())
    finally:
        os.environ.pop("PYTHON_BASE_IMAGE", None)
    if 'PYTHON_BASE_IMAGE: "docker.m.daocloud.io/library/python:3.11-slim-bookworm"' not in mirrored:
        fail("daocloud python base was not accepted")
    if contract()["db_image"] not in mirrored or "pull_policy: never" not in mirrored:
        fail("database pin changed while selecting a build base")
    os.environ["NODE_BASE_IMAGE"] = "example.invalid/node:22-bookworm-slim"
    try:
        if raises(lambda: checks.select_base("node")) is None:
            fail("arbitrary node registry was accepted")
    finally:
        os.environ.pop("NODE_BASE_IMAGE", None)
    if "\n  web:\n" in text:
        fail("runtime file includes web")
    checks.check_rendered(text, contract())
    bad = text.replace('      - "5433:5432"', '      - "5433:5432"\n      - "5432:5432"', 1)
    if raises(lambda: checks.check_rendered(bad, contract())) is None:
        fail("merged default db port was accepted")


def test_old_compose_version_is_not_a_blocker() -> None:
    text = checks.describe_compose_version("2.2.1")
    if "2.2.1" not in text or "2.24" not in text:
        fail(text)


def test_health_requires_database() -> None:
    checks.assert_healthy('{"status":"healthy","database":true,"ai_configured":false}')
    if raises(lambda: checks.assert_healthy('{"status":"degraded","database":false}')) is None:
        fail("degraded health was accepted")
    if raises(lambda: checks.assert_healthy('{"status":"healthy","database":"true"}')) is None:
        fail("string database flag was accepted")


def test_discover_hides_secrets_and_requires_identity() -> None:
    secret = "sk-live-secret-do-not-print"
    root = "/tmp/crawler-discover"
    os.makedirs(root, exist_ok=True)

    def container(service, ports=None, volume=False, health="healthy"):
        bindings = {}
        for container_port, host_port in ports or []:
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
                "Name": "f" * 64,
                "Destination": "/data",
            })
        state = {"Status": "running"}
        if health:
            state["Health"] = {"Status": health}
        return {
            "Id": {"db": "a", "redis": "b", "api": "c", "worker": "d", "scheduler": "e"}[service] * 64,
            "Name": f"/crawler-platform-{service}-1",
            "Image": "sha256:" + {"db": "1", "redis": "2", "api": "3", "worker": "4", "scheduler": "5"}[service] * 64,
            "Config": {
                "Image": {
                    "db": "docker.m.daocloud.io/library/postgres:15-alpine",
                    "redis": "docker.m.daocloud.io/library/redis:7-alpine",
                }.get(service, ""),
                "Labels": {
                    "com.docker.compose.project": "crawler-platform",
                    "com.docker.compose.project.working_dir": root,
                    "com.docker.compose.service": service,
                    "com.docker.compose.oneoff": "False",
                },
                "Env": [f"OPENAI_API_KEY={secret}", "DATABASE_URL=postgresql://user:pass@db:5432/crawler_db"],
            },
            "State": state,
            "HostConfig": {"PortBindings": bindings or None},
            "Mounts": mounts,
            "NetworkSettings": {"Networks": {"crawler-platform_default": {}}},
        }

    containers = [
        container("db", [(5432, 5433)], volume=True),
        container("redis", [(6379, 6380)]),
        container("api", [(8000, 8010)]),
        container("worker", health=None),
        container("scheduler", health=None),
    ]
    found = checks.discover(containers, root, "crawler-platform", "crawler-platform_postgres_data")
    encoded = json.dumps(found)
    if secret in encoded or "pass@db" in encoded:
        fail("contract contains a secret")
    if found["network"] != "crawler-platform_default":
        fail("network was not preserved")
    if found["db_image"] != "sha256:" + "1" * 64 or "postgres:15-alpine" in found["db_image"]:
        fail("db image was not the observed sha256")
    if found["redis_volume"] != "f" * 64:
        fail("redis data volume was not preserved")
    pinned = checks.runtime_yaml(found)
    if "postgres:15-alpine" in pinned or "redis:7-alpine" in pinned or "daocloud.io" in pinned:
        fail("runtime selected a registry tag instead of the running image id")
    if found["db_image"] not in pinned or found["redis_image"] not in pinned or "pull_policy: never" not in pinned:
        fail("runtime did not pin the observed image ids")
    bad = json.loads(json.dumps(containers))
    bad[0]["HostConfig"]["PortBindings"]["5432/tcp"].append({"HostIp": "", "HostPort": "5432"})
    if raises(lambda: checks.discover(bad, root, "crawler-platform", "crawler-platform_postgres_data")) is None:
        fail("extra published db port was accepted")


def test_static_copy_stays_inside_crawler() -> None:
    with tempfile.TemporaryDirectory() as raw:
        os.environ["CRAWLER_TESTING"] = "1"
        os.environ["CRAWLER_TEST_TMP"] = raw
        base = Path(raw)
        source = base / "dist"
        destination = base / "www" / "crawler"
        homepage = base / "www" / "homepage.html"
        member = base / "www" / "member"
        source.mkdir()
        destination.mkdir(parents=True)
        (source / "index.html").write_text('<script src="/crawler/assets/app.js"></script>', encoding="utf-8")
        (source / "app.js").write_text("fetch('/api/v1')", encoding="utf-8")
        (destination / "old.js").write_text("old", encoding="utf-8")
        homepage.write_text("keep-home", encoding="utf-8")
        member.mkdir()
        (member / "index.html").write_text("keep-member", encoding="utf-8")
        checks.install_static(source, str(destination))
        if (destination / "old.js").exists() or homepage.read_text() != "keep-home":
            fail("static sync touched a file outside the replacement set")
        if (member / "index.html").read_text() != "keep-member":
            fail("member path was modified")
        poisoned = base / "bad"
        poisoned.mkdir()
        (poisoned / "index.html").write_text("/crawler/ localhost:8000", encoding="utf-8")
        if raises(lambda: checks.install_static(poisoned, str(destination))) is None:
            fail("localhost:8000 asset was copied")
        if "keep-home" not in homepage.read_text():
            fail("failed copy still changed homepage")


def test_sync_preserves_server_files_and_omits_env() -> None:
    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw)
        stage = base / "stage"
        server = base / "server"
        secret = "sk-test-SECRET-do-not-leak"
        (stage / "api/scripts").mkdir(parents=True)
        (stage / "jobs").mkdir()
        (stage / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
        (stage / "api/Dockerfile").write_text("FROM python:3.10-slim\n", encoding="utf-8")
        (stage / "api/scripts/migrate_v2.py").write_text("print('ok')\n", encoding="utf-8")
        (stage / "README.md").write_text("new\n", encoding="utf-8")
        (stage / "jobs/deploy.sh").write_text("echo v2\n", encoding="utf-8")
        server.mkdir()
        (server / "jobs").mkdir()
        (server / ".env").write_text(f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
        (server / "docker-compose.override.yml").write_text("PORTS-KEEP\n", encoding="utf-8")
        (server / "jobs/create-admin.sh").write_text("ADMIN-KEEP\n", encoding="utf-8")
        (server / "jobs/custom-server.sh").write_text("CUSTOM-KEEP\n", encoding="utf-8")
        (server / "README.md").write_text("old\n", encoding="utf-8")
        backup = base / "code.tar.gz"
        tree.make_backup(server, backup)
        with tarfile.open(backup, "r:gz") as archive:
            names = archive.getnames()
            if any(name == ".env" or name.endswith("/.env") for name in names):
                fail("backup includes .env")
            for member in archive.getmembers():
                if member.isfile() and secret.encode() in archive.extractfile(member).read():
                    fail("backup contains the secret")
        tree.sync_tree(stage, server, server / "jobs/state/upstream-manifest.txt")
        if (server / ".env").read_text() != f"OPENAI_API_KEY={secret}\n":
            fail(".env changed")
        if "PORTS-KEEP" not in (server / "docker-compose.override.yml").read_text():
            fail("override changed")
        if "ADMIN-KEEP" not in (server / "jobs/create-admin.sh").read_text():
            fail("create-admin changed")
        if "CUSTOM-KEEP" not in (server / "jobs/custom-server.sh").read_text():
            fail("server-only job was deleted")
        if (server / "README.md").read_text() != "new\n":
            fail("upstream file was not updated")
        (stage / "removed.txt").write_text("gone\n", encoding="utf-8")
        tree.sync_tree(stage, server, server / "jobs/state/upstream-manifest.txt")
        (stage / "removed.txt").unlink()
        tree.sync_tree(stage, server, server / "jobs/state/upstream-manifest.txt")
        if (server / "removed.txt").exists():
            fail("removed upstream file was kept")
        if not (server / "jobs/custom-server.sh").exists():
            fail("custom job deleted with upstream files")


def test_mirrors_and_dockerfile() -> None:
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "etc").mkdir()
        (root / "etc/os-release").write_text('VERSION_ID="12"\nVERSION_CODENAME=bookworm\n', encoding="utf-8")
        sources = root / "etc/apt/sources.list.d"
        sources.mkdir(parents=True)
        (sources / "debian.sources").write_text(
            "Types: deb\nURIs: http://deb.debian.org/debian\nSuites: bookworm bookworm-updates\n"
            "Components: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\n"
            "Types: deb\nURIs: http://deb.debian.org/debian-security\nSuites: bookworm-security\n"
            "Components: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n",
            encoding="utf-8",
        )
        mirrors.apply_apt(
            root,
            "https://mirrors.aliyun.com/debian",
            "https://mirrors.aliyun.com/debian-security",
        )
        rewritten = (sources / "debian.sources").read_text(encoding="utf-8")
        timeout_conf = (root / "etc/apt/apt.conf.d/99-crawler-timeouts").read_text(encoding="utf-8")
        if "deb.debian.org" in rewritten or "Signed-By" not in rewritten:
            fail("mirror rewrite dropped signature metadata or left the official host")
        if "Acquire::http::Timeout" not in timeout_conf or "Check-Valid-Until" in timeout_conf:
            fail("apt timeout config is wrong")
        (root / "etc/os-release").write_text('VERSION_ID="11"\nVERSION_CODENAME=bullseye\n', encoding="utf-8")
        if raises(lambda: mirrors.apply_apt(root, "", "")) is None:
            fail("bullseye base was accepted")
    env = Path(tempfile.mkdtemp()) / "mirrors.env"
    env.write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")
    if raises(lambda: mirrors.shell_exports(env)) is None:
        fail("secret key in mirrors.env was accepted")
    env.write_text(
        "APT_MIRROR=https://mirrors.aliyun.com/debian\n"
        "APT_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security\n"
        "PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple\n",
        encoding="utf-8",
    )
    exported = mirrors.shell_exports(env)
    if "sk-" in exported or "OPENAI" in exported:
        fail("shell export leaked a secret")
    for npm_url in ("https://registry.npmmirror.com", "https://registry.npmmirror.com/"):
        env.write_text(f"NPM_REGISTRY={npm_url}\n", encoding="utf-8")
        if mirrors.parse_env_file(env)["NPM_REGISTRY"] != "https://registry.npmmirror.com":
            fail(f"npm registry rejected: {npm_url}")
    example = mirrors.parse_env_file(ROOT / "jobs/mirrors.env.example")
    if example.get("NPM_REGISTRY") != "https://registry.npmmirror.com":
        fail("mirrors.env.example npm registry is not accepted")
    command = mirrors.pip_command("https://mirrors.aliyun.com/pypi/simple")
    if "--trusted-host" in command or "--retries" not in command:
        fail(command)
    dockerfile = (ROOT / "api/Dockerfile.deploy").read_text(encoding="utf-8")
    if "python:3.11-slim-bookworm" not in dockerfile or "bullseye" in dockerfile:
        fail("deploy image is not pinned to Debian 12")
    lowered = dockerfile.lower()
    for banned in ("fix-missing", "check-valid-until", "allowinsecure", "trusted=yes"):
        if banned in lowered:
            fail(f"dockerfile contains {banned}")
    if "python:3.10-slim" not in (ROOT / "api/Dockerfile").read_text(encoding="utf-8"):
        fail("stock Dockerfile was changed")


def test_repo_url() -> None:
    os.environ.pop("CRAWLER_TESTING", None)
    if raises(lambda: checks.assert_repo("https://ghfast.top/github.com/machachong/crawler-platform.git")) is None:
        fail("ghfast was accepted")
    if checks.assert_repo("https://github.com/machachong/crawler-platform.git") != \
            "https://github.com/machachong/crawler-platform.git":
        fail("official repo was rejected")


def main() -> None:
    test_runtime_file_replaces_ports()
    test_old_compose_version_is_not_a_blocker()
    test_health_requires_database()
    test_discover_hides_secrets_and_requires_identity()
    test_static_copy_stays_inside_crawler()
    test_sync_preserves_server_files_and_omits_env()
    test_mirrors_and_dockerfile()
    test_repo_url()
    print("python guards ok")


if __name__ == "__main__":
    main()
