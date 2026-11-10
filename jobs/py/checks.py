#!/usr/bin/env python3
"""Compose identity, port, health, and static-directory guards."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REQUIRED_PORTS = {
    "api": {"host_port": 8010, "container_port": 8000},
    "db": {"host_port": 5433, "container_port": 5432},
    "redis": {"host_port": 6380, "container_port": 6379},
}
FORBIDDEN_HOST_PORTS = {8000, 5432, 6379, 3000}
DEFAULT_SERVICES = {"api", "db", "redis", "scheduler", "worker"}
APP_SERVICES = {"api", "worker", "scheduler"}
PRODUCTION_STATIC = Path("/var/www/zhengaihe/crawler")
PRODUCTION_ROOT = Path("/home/workspace/crawler-platform")
PYTHON_BASE_DEFAULT = "python:3.11-slim-bookworm"
NODE_BASE_DEFAULT = "node:22-bookworm-slim"
BASE_IMAGES = {
    "python": {
        PYTHON_BASE_DEFAULT,
        "docker.io/library/python:3.11-slim-bookworm",
        "docker.m.daocloud.io/library/python:3.11-slim-bookworm",
    },
    "node": {
        NODE_BASE_DEFAULT,
        "docker.io/library/node:22-bookworm-slim",
        "docker.m.daocloud.io/library/node:22-bookworm-slim",
    },
}


class GuardError(SystemExit):
    pass


def parse_version(text: str) -> tuple[int, int, int]:
    cleaned = text.strip().lstrip("vV")
    parts = cleaned.split(".")
    numbers: list[int] = []
    for part in parts[:3]:
        if not part.isdigit():
            raise GuardError(f"无法识别 Compose 版本: {text.strip()}")
        numbers.append(int(part))
    while len(numbers) < 3:
        numbers.append(0)
    return numbers[0], numbers[1], numbers[2]


def describe_compose_version(text: str) -> str:
    cleaned = " ".join(text.split()) or "未知"
    try:
        version = parse_version(cleaned)
        shown = f"{version[0]}.{version[1]}.{version[2]}"
    except GuardError:
        shown = cleaned
    return (
        f"Compose 版本: {shown}。"
        "部署使用单独的完整 compose 文件，不与仓库文件合并，不使用 !override，"
        "因此不要求 Compose 2.24，也不会升级 Docker。"
    )


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise GuardError(f"无法解析 JSON: {path.name}") from exc


def _label(container: dict, key: str) -> str:
    labels = (container.get("Config") or {}).get("Labels") or {}
    return str(labels.get(key) or "")


def _norm_ip(value: str | None) -> str:
    if value in (None, "", "0.0.0.0"):
        return ""
    return str(value)


def _same_path(left: str, right: str) -> bool:
    left_path, right_path = Path(left), Path(right)
    if left_path.exists() and right_path.exists():
        return left_path.resolve() == right_path.resolve()
    return os.path.normpath(left) == os.path.normpath(right)


def _bindings(container: dict) -> list[dict]:
    raw = (container.get("HostConfig") or {}).get("PortBindings") or {}
    found = []
    for key, bindings in raw.items():
        if "/" not in key:
            raise GuardError("端口绑定格式无法识别")
        container_port, protocol = key.split("/", 1)
        if protocol != "tcp" or not container_port.isdigit():
            raise GuardError("只接受 TCP 端口绑定")
        for binding in bindings or []:
            host_port = str(binding.get("HostPort") or "")
            if not host_port.isdigit():
                raise GuardError("主机端口不是数字")
            found.append({
                "host_ip": _norm_ip(binding.get("HostIp")),
                "host_port": int(host_port),
                "container_port": int(container_port),
            })
    return found


def _reject_foreign(container: dict) -> None:
    blob = " ".join([
        container.get("Name") or "",
        _label(container, "com.docker.compose.service"),
        _label(container, "com.docker.compose.project"),
    ]).lower()
    if "qingji" in blob or "/member" in blob or "homepage" in blob:
        raise GuardError("检查到 qingji、member 或 homepage 容器，拒绝继续")


def discover(containers: list, root: str, expect_project: str, expect_volume: str) -> dict:
    if not isinstance(containers, list):
        raise GuardError("docker inspect 结果不是数组")
    root_path = Path(root)
    if root_path.resolve() == PRODUCTION_ROOT and os.environ.get("CRAWLER_TESTING") == "1":
        raise GuardError("禁止在生产目录启用 CRAWLER_TESTING")
    selected = []
    for container in containers:
        _reject_foreign(container)
        if _label(container, "com.docker.compose.oneoff").lower() == "true":
            continue
        selected.append(container)
    if not selected:
        raise GuardError("未发现本目录的 Compose 容器，拒绝创建新项目或新数据库卷")
    projects = {_label(item, "com.docker.compose.project") for item in selected}
    workdirs = {_label(item, "com.docker.compose.project.working_dir") for item in selected}
    if len(projects) != 1 or "" in projects:
        raise GuardError("运行中的容器不属于同一个 Compose 项目")
    if len(workdirs) != 1 or not _same_path(next(iter(workdirs)), root):
        raise GuardError("运行中的项目工作目录不是当前目录，拒绝继续")
    project = next(iter(projects))
    by_service: dict[str, dict] = {}
    for container in selected:
        service = _label(container, "com.docker.compose.service")
        status = ((container.get("State") or {}).get("Status") or "")
        if service in by_service and status == "running":
            raise GuardError(f"服务 {service} 有多个容器")
        if service not in by_service or status == "running":
            by_service[service] = container
    running = {
        service for service, container in by_service.items()
        if ((container.get("State") or {}).get("Status") == "running")
    }
    if "web" in running:
        raise GuardError("web 服务正在运行。生产启动不能启用 local-web")
    missing = DEFAULT_SERVICES - running
    extra = running - DEFAULT_SERVICES
    if missing or extra:
        raise GuardError(f"运行中的服务必须正好是 api、db、redis、worker、scheduler，当前运行: {sorted(running)}")
    for service in ("api", "db", "redis"):
        health = (((by_service[service].get("State") or {}).get("Health") or {}).get("Status"))
        if health != "healthy":
            raise GuardError(f"{service} 容器健康状态不是 healthy")
    ports = {}
    for service, expected in REQUIRED_PORTS.items():
        bindings = _bindings(by_service[service])
        if len(bindings) != 1:
            raise GuardError(f"{service} 必须只有一条主机端口绑定")
        binding = bindings[0]
        if binding["host_port"] != expected["host_port"] or binding["container_port"] != expected["container_port"]:
            raise GuardError(
                f"{service} 端口是 {binding['host_port']}->{binding['container_port']}，"
                f"预期 {expected['host_port']}->{expected['container_port']}"
            )
        if binding["host_port"] in FORBIDDEN_HOST_PORTS:
            raise GuardError(f"禁止的主机端口: {binding['host_port']}")
        ports[service] = binding
    for service in ("worker", "scheduler"):
        if _bindings(by_service[service]):
            raise GuardError(f"{service} 不应发布主机端口")
    for container in selected:
        for binding in _bindings(container):
            if binding["host_port"] in FORBIDDEN_HOST_PORTS:
                raise GuardError(f"禁止的主机端口: {binding['host_port']}")
    db_mounts = [
        mount for mount in (by_service["db"].get("Mounts") or [])
        if mount.get("Destination") == "/var/lib/postgresql/data"
    ]
    if len(db_mounts) != 1 or db_mounts[0].get("Type") != "volume" or not db_mounts[0].get("Name"):
        raise GuardError("db 没有唯一的具名数据库卷，拒绝继续")
    volume = str(db_mounts[0]["Name"])
    redis_mounts = [
        mount for mount in (by_service["redis"].get("Mounts") or [])
        if mount.get("Destination") == "/data" and mount.get("Type") == "volume" and mount.get("Name")
    ]
    if len(redis_mounts) != 1:
        raise GuardError("redis 没有唯一的 /data 卷，拒绝继续")
    redis_volume = str(redis_mounts[0]["Name"])
    network = _shared_network(by_service, project)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", project):
        raise GuardError("项目名含有不支持的字符")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", volume):
        raise GuardError("数据库卷名含有不支持的字符")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", network):
        raise GuardError("网络名含有不支持的字符")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", redis_volume):
        raise GuardError("redis 卷名含有不支持的字符")
    if project != expect_project or volume != expect_volume:
        raise GuardError(
            f"运行中的 Compose 项目是 {project}，数据库卷是 {volume}。"
            f"预期项目 {expect_project}、卷 {expect_volume}。"
            "预检未修改容器。请先与备份核对；确认无误后才设置 "
            "CRAWLER_EXPECT_PROJECT 和 CRAWLER_EXPECT_VOLUME。"
        )
    for key, container in (("db", by_service["db"]), ("redis", by_service["redis"])):
        image = str(container.get("Image") or "")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise GuardError(f"{key} 镜像 ID 不是 sha256 摘要，拒绝继续")
        cid = str(container.get("Id") or "")
        if not re.fullmatch(r"[0-9a-f]{12,64}", cid):
            raise GuardError(f"{key} 容器 ID 无法识别")
    return {
        "project": project,
        "working_dir": str(root_path.resolve()),
        "volume": volume,
        "db_container": by_service["db"]["Id"],
        "redis_container": by_service["redis"]["Id"],
        "db_image": by_service["db"]["Image"],
        "redis_image": by_service["redis"]["Image"],
        "redis_volume": redis_volume,
        "network": network,
        "ports": ports,
    }


def _network_names(container: dict) -> set[str]:
    networks = (container.get("NetworkSettings") or {}).get("Networks") or {}
    if not isinstance(networks, dict):
        raise GuardError("容器网络信息无法识别")
    return set(networks)


def _shared_network(by_service: dict[str, dict], project: str) -> str:
    db_networks = _network_names(by_service["db"])
    preferred = f"{project}_default"
    if preferred in db_networks:
        chosen = preferred
    elif len(db_networks) == 1:
        chosen = next(iter(db_networks))
    else:
        defaults = sorted(name for name in db_networks if name.endswith("_default"))
        if len(defaults) != 1:
            raise GuardError("db 连接了多个网络，无法确定要复用的网络")
        chosen = defaults[0]
    for service in ("redis", "api", "worker", "scheduler"):
        if chosen not in _network_names(by_service[service]):
            raise GuardError(f"{service} 不在数据库所在网络 {chosen} 上")
    return chosen


def app_running(containers: list) -> bool:
    if not isinstance(containers, list):
        raise GuardError("docker inspect 结果不是数组")
    for container in containers:
        if _label(container, "com.docker.compose.oneoff").lower() == "true":
            continue
        service = _label(container, "com.docker.compose.service")
        status = ((container.get("State") or {}).get("Status") or "")
        if service in APP_SERVICES and status == "running":
            return True
    return False


def _port_line(binding: dict) -> str:
    host_ip = binding["host_ip"]
    host_port = binding["host_port"]
    container_port = binding["container_port"]
    if host_ip and not re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", host_ip):
        raise GuardError("主机绑定地址不是 IPv4，拒绝生成端口覆盖")
    if host_ip:
        return f"{host_ip}:{host_port}:{container_port}"
    return f"{host_port}:{container_port}"


def runtime_yaml(contract: dict) -> str:
    """Complete compose file used alone. Ports are replaced, not merged."""
    api = _port_line(contract["ports"]["api"])
    db = _port_line(contract["ports"]["db"])
    redis = _port_line(contract["ports"]["redis"])
    for key in ("db_image", "redis_image"):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(contract.get(key) or "")):
            raise GuardError("拒绝把非 sha256 的 db/redis 镜像写入部署文件")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", str(contract.get("redis_volume") or "")):
        raise GuardError("拒绝把无法识别的 redis 卷写入部署文件")
    python_base = select_base("python")
    text = f"""# 单独交给 docker compose -f 使用，不要再叠加 docker-compose.yml 或 override。
# 主机端口只保留当前容器已经发布的映射。db/redis 使用预检看到的本地镜像 ID。
services:
  db:
    image: {contract["db_image"]}
    pull_policy: never
    environment:
      POSTGRES_USER: user
      POSTGRES_PASSWORD: pass
      POSTGRES_DB: crawler_db
    ports:
      - "{db}"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U user -d crawler_db"]
      interval: 5s
      timeout: 5s
      retries: 12
  redis:
    image: {contract["redis_image"]}
    pull_policy: never
    volumes:
      - redis_data:/data
    ports:
      - "{redis}"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 12
  api:
    build:
      context: ./api
      dockerfile: Dockerfile.deploy
      args:
        APT_MIRROR: ${{APT_MIRROR:-}}
        APT_SECURITY_MIRROR: ${{APT_SECURITY_MIRROR:-}}
        PIP_INDEX_URL: ${{PIP_INDEX_URL:-}}
        PYTHON_BASE_IMAGE: "{python_base}"
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    volumes:
      - ./api:/app
    ports:
      - "{api}"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 12
    environment:
      DATABASE_URL: postgresql://user:pass@db:5432/crawler_db
      REDIS_URL: redis://redis:6379/0
      CORS_ORIGINS: ${{CORS_ORIGINS:-http://localhost:3000,http://127.0.0.1:3000}}
      AUTH_SESSION_HOURS: ${{AUTH_SESSION_HOURS:-8}}
      PUBLIC_BASE_URL: ${{PUBLIC_BASE_URL:-}}
      OPENAI_API_KEY: ${{OPENAI_API_KEY:-}}
      OPENAI_API_BASE: ${{OPENAI_API_BASE:-https://api.openai.com/v1}}
      AI_MODEL: ${{AI_MODEL:-gpt-4o-mini}}
      AI_CHUNK_CHARS: ${{AI_CHUNK_CHARS:-12000}}
      CRAWLER_USER_AGENT: ${{CRAWLER_USER_AGENT:-CrawlerPlatformBot/1.0 (+https://github.com/machachong/crawler-platform; respects robots.txt)}}
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
  worker:
    build:
      context: ./api
      dockerfile: Dockerfile.deploy
      args:
        APT_MIRROR: ${{APT_MIRROR:-}}
        APT_SECURITY_MIRROR: ${{APT_SECURITY_MIRROR:-}}
        PIP_INDEX_URL: ${{PIP_INDEX_URL:-}}
        PYTHON_BASE_IMAGE: "{python_base}"
    command: celery -A app.worker.tasks worker --loglevel=info --concurrency=2
    volumes:
      - ./api:/app
    environment:
      DATABASE_URL: postgresql://user:pass@db:5432/crawler_db
      REDIS_URL: redis://redis:6379/0
      OPENAI_API_KEY: ${{OPENAI_API_KEY:-}}
      OPENAI_API_BASE: ${{OPENAI_API_BASE:-https://api.openai.com/v1}}
      AI_MODEL: ${{AI_MODEL:-gpt-4o-mini}}
      AI_CHUNK_CHARS: ${{AI_CHUNK_CHARS:-12000}}
      CRAWLER_USER_AGENT: ${{CRAWLER_USER_AGENT:-CrawlerPlatformBot/1.0 (+https://github.com/machachong/crawler-platform; respects robots.txt)}}
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
  scheduler:
    build:
      context: ./api
      dockerfile: Dockerfile.deploy
      args:
        APT_MIRROR: ${{APT_MIRROR:-}}
        APT_SECURITY_MIRROR: ${{APT_SECURITY_MIRROR:-}}
        PIP_INDEX_URL: ${{PIP_INDEX_URL:-}}
        PYTHON_BASE_IMAGE: "{python_base}"
    command: python scripts/scheduler.py
    restart: unless-stopped
    volumes:
      - ./api:/app
    environment:
      DATABASE_URL: postgresql://user:pass@db:5432/crawler_db
      REDIS_URL: redis://redis:6379/0
    depends_on:
      api:
        condition: service_healthy
      redis:
        condition: service_healthy
networks:
  default:
    external:
      name: {contract["network"]}
volumes:
  postgres_data:
    external:
      name: {contract["volume"]}
  redis_data:
    external:
      name: {contract["redis_volume"]}
"""
    for forbidden in ("!override", "5432:5432", "6379:6379", "8000:8000", "3000:3000", "\n  web:\n", "postgres:15-alpine", "redis:7-alpine"):
        if forbidden in text:
            raise GuardError("生成的部署文件含有禁止内容")
    if contract["volume"] not in text or contract["network"] not in text:
        raise GuardError("生成的部署文件缺少现有卷或网络")
    return text


def _published(service: dict) -> list[dict]:
    found = []
    for item in service.get("ports") or []:
        protocol = item.get("protocol") or "tcp"
        if protocol != "tcp":
            raise GuardError("Compose 配置含有非 TCP 端口")
        published = item.get("published")
        target = item.get("target")
        if isinstance(published, str) and published.isdigit():
            published = int(published)
        if not isinstance(published, int) or not isinstance(target, int):
            raise GuardError("Compose 端口发布值无法识别")
        found.append({
            "host_ip": _norm_ip(item.get("host_ip")),
            "host_port": published,
            "container_port": target,
        })
    return found


def _external_name(section: dict, key: str) -> tuple[str, bool]:
    item = (section or {}).get(key) or {}
    external = item.get("external")
    name = item.get("name")
    if isinstance(external, dict):
        name = external.get("name") or name
        external = True
    return str(name or ""), bool(external)


def check_config(config: dict, contract: dict) -> None:
    if config.get("name") not in (None, "", contract["project"]):
        raise GuardError("渲染后的项目名与正在运行的项目不一致")
    services = config.get("services") or {}
    if "web" in services:
        raise GuardError("部署文件不应包含 web")
    if set(services) != DEFAULT_SERVICES:
        raise GuardError(f"部署服务不正确: {sorted(services)}")
    volume, volume_external = _external_name(config.get("volumes") or {}, "postgres_data")
    if volume != contract["volume"] or not volume_external:
        raise GuardError("数据库卷必须复用现有卷并标记为 external，拒绝新建空卷")
    network, network_external = _external_name(config.get("networks") or {}, "default")
    if network != contract["network"] or not network_external:
        raise GuardError("网络必须复用现有网络并标记为 external")
    host_ports = set()
    for service in services.values():
        for binding in _published(service):
            host_ports.add(binding["host_port"])
            if binding["host_port"] in FORBIDDEN_HOST_PORTS:
                raise GuardError(f"渲染结果含有禁止的主机端口 {binding['host_port']}")
    for service, expected in contract["ports"].items():
        bindings = _published(services.get(service) or {})
        if bindings != [expected]:
            raise GuardError(f"渲染后的 {service} 端口与当前容器不一致")
    if _published(services.get("worker") or {}) or _published(services.get("scheduler") or {}):
        raise GuardError("worker 或 scheduler 会发布主机端口")
    if host_ports != {8010, 5433, 6380}:
        raise GuardError(f"渲染后的主机端口是 {sorted(host_ports)}，只允许 8010、5433、6380")


def rendered_host_ports(text: str) -> set[int]:
    ports = set()
    short = re.compile(
        r'(?m)^\s*-\s*"?(?:(?:\d{1,3}\.){3}\d{1,3}:)?(\d{2,5}):(\d{2,5})"?\s*$'
    )
    published = re.compile(r'(?m)^\s*published:\s*"?(\d{2,5})"?\s*$')
    ports.update(int(match.group(1)) for match in short.finditer(text))
    ports.update(int(match.group(1)) for match in published.finditer(text))
    return ports


def check_rendered(text: str, contract: dict) -> None:
    if "!override" in text:
        raise GuardError("渲染结果含有 !override")
    if re.search(r"(?m)^  web:\s*$", text):
        raise GuardError("渲染结果包含 web")
    for forbidden in ("5432:5432", "6379:6379", "8000:8000", "3000:3000"):
        if forbidden in text:
            raise GuardError(f"渲染结果含有默认端口 {forbidden}")
    ports = rendered_host_ports(text)
    if ports != {8010, 5433, 6380}:
        raise GuardError(f"渲染后的主机端口是 {sorted(ports)}，只允许 8010、5433、6380")
    if contract["volume"] not in text:
        raise GuardError("渲染结果没有现有数据库卷名")
    if contract.get("redis_volume") and contract["redis_volume"] not in text:
        raise GuardError("渲染结果没有现有 redis 数据卷")
    for key in ("db_image", "redis_image"):
        image = contract.get(key) or ""
        if image and image not in text:
            raise GuardError("渲染结果没有钉住当前 db/redis 镜像")
    if "postgres:15-alpine" in text or "redis:7-alpine" in text:
        raise GuardError("渲染结果使用了本机没有的默认数据库镜像名")
    if contract["network"] not in text:
        raise GuardError("渲染结果没有现有网络名")
    if "external" not in text:
        raise GuardError("渲染结果没有把卷或网络标为 external")


def _mount_name(container: dict, destination: str) -> str:
    names = [
        str(mount.get("Name") or "")
        for mount in (container.get("Mounts") or [])
        if mount.get("Destination") == destination and mount.get("Type") == "volume"
    ]
    return names[0] if len(names) == 1 else ""


def assert_unchanged(contract: dict, containers: list) -> None:
    found = {}
    for container in containers:
        if _label(container, "com.docker.compose.oneoff").lower() == "true":
            continue
        service = _label(container, "com.docker.compose.service")
        if ((container.get("State") or {}).get("Status")) == "running":
            found[service] = container
    for service, cid_key, image_key in (
        ("db", "db_container", "db_image"),
        ("redis", "redis_container", "redis_image"),
    ):
        current = found.get(service)
        if not current or current.get("Id") != contract[cid_key] or current.get("Image") != contract[image_key]:
            raise GuardError(f"{service} 容器或镜像在本次部署中发生变化，已停止后续步骤。不要执行 volume prune。")
    db_mount = _mount_name(found.get("db") or {}, "/var/lib/postgresql/data")
    redis_mount = _mount_name(found.get("redis") or {}, "/data")
    if db_mount != contract.get("volume") or redis_mount != contract.get("redis_volume"):
        raise GuardError("db 或 redis 的数据卷在本次部署中发生变化，已停止后续步骤。不要执行 volume prune。")


def assert_healthy(body: str) -> None:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise GuardError("健康检查不是 JSON") from exc
    if not isinstance(data, dict) or data.get("status") != "healthy" or data.get("database") is not True:
        status = data.get("status") if isinstance(data, dict) else None
        database = data.get("database") if isinstance(data, dict) else None
        raise GuardError(f"健康检查未通过: status={status} database={database}")


def fetch_health(url: str, timeout: float) -> str:
    if url != "http://127.0.0.1:8010/api/v1/health" and os.environ.get("CRAWLER_TESTING") != "1":
        raise GuardError("健康检查只访问 127.0.0.1:8010")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                raise GuardError(f"健康检查 HTTP {response.status}")
            return response.read().decode("utf-8", errors="replace")
    except GuardError:
        raise
    except Exception as exc:
        raise GuardError(f"健康检查连接失败: {exc.__class__.__name__}") from exc


def static_destination(dst: str) -> Path:
    path = Path(dst)
    if path.is_symlink():
        raise GuardError("静态目录不能是符号链接")
    resolved = path.resolve()
    if {"qingji", "member", "homepage"} & set(resolved.parts):
        raise GuardError("拒绝写入 qingji、member 或 homepage 路径")
    testing = os.environ.get("CRAWLER_TESTING") == "1"
    if testing:
        base = Path(os.environ.get("CRAWLER_TEST_TMP", ""))
        if not base.exists():
            raise GuardError("测试缺少 CRAWLER_TEST_TMP")
        base_resolved = base.resolve()
        if base_resolved != resolved and base_resolved not in resolved.parents:
            raise GuardError("测试静态目录必须在 CRAWLER_TEST_TMP 内")
        if resolved.name != "crawler":
            raise GuardError("静态目录名必须是 crawler")
        if not resolved.is_dir():
            raise GuardError("静态目录不存在")
        return resolved
    if resolved != PRODUCTION_STATIC:
        raise GuardError("静态目录只能是 /var/www/zhengaihe/crawler")
    if not resolved.is_dir():
        raise GuardError("静态目录不存在")
    return resolved


def install_static(src: Path, dst: str) -> None:
    destination = static_destination(dst)
    source = src.resolve()
    index = source / "index.html"
    if not index.is_file():
        raise GuardError("构建产物缺少 index.html")
    if "/crawler/" not in index.read_text(encoding="utf-8", errors="replace"):
        raise GuardError("index.html 未使用 /crawler/ 基础路径")
    files: list[Path] = []
    for file in source.rglob("*"):
        if file.is_symlink():
            raise GuardError("构建产物含符号链接")
        if file.is_file():
            if b"localhost:8000" in file.read_bytes() or b"127.0.0.1:8000" in file.read_bytes():
                raise GuardError("静态产物指向本机 8000，已拒绝覆盖")
            files.append(file)
    if not files:
        raise GuardError("构建产物为空，已拒绝覆盖静态目录")
    for file in destination.rglob("*"):
        if file.is_symlink():
            raise GuardError("静态目录含符号链接，已拒绝修改")
    keep = set()
    for file in files:
        relative = file.relative_to(source).as_posix()
        if any(part in ("", ".", "..") for part in relative.split("/")):
            raise GuardError("构建产物路径非法")
        target = destination.joinpath(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o755)
        temporary = target.with_name(target.name + ".tmp-deploy")
        temporary.write_bytes(file.read_bytes())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
        keep.add(relative)
    for file in sorted((path for path in destination.rglob("*")), key=lambda item: len(item.parts), reverse=True):
        relative = file.relative_to(destination).as_posix()
        if file.is_file() and relative not in keep and not relative.endswith(".tmp-deploy"):
            file.unlink()
        elif file.is_dir():
            try:
                file.rmdir()
            except OSError:
                pass
    os.chmod(destination, 0o755)


def assert_disk(paths: list[str], minimum_mb: int) -> None:
    if minimum_mb <= 0:
        return
    for raw in paths:
        path = Path(raw)
        if not path.exists():
            continue
        stats = os.statvfs(path)
        free_mb = stats.f_bavail * stats.f_frsize // (1024 * 1024)
        if free_mb < minimum_mb:
            raise GuardError(f"{path} 可用空间 {free_mb}MB，低于 {minimum_mb}MB，拒绝继续")


def assert_repo(url: str) -> str:
    testing = os.environ.get("CRAWLER_TESTING") == "1"
    if "ghfast" in url.lower() or "@" in url or "token" in url.lower():
        raise GuardError("仓库地址不安全")
    official = "https://github.com/machachong/crawler-platform.git"
    if not testing:
        if url != official:
            raise GuardError(f"只允许从 {official} 更新")
        return url
    path = Path(url)
    if not path.is_dir():
        raise GuardError("测试仓库必须是本地目录")
    resolved = str(path.resolve())
    if not (resolved.startswith("/tmp/") or resolved.startswith("/var/tmp/")):
        raise GuardError("测试仓库必须位于 /tmp 或 /var/tmp")
    return resolved


def select_base(kind: str) -> str:
    if kind not in BASE_IMAGES:
        raise GuardError("基础镜像种类只能是 python 或 node")
    env_name = "PYTHON_BASE_IMAGE" if kind == "python" else "NODE_BASE_IMAGE"
    default = PYTHON_BASE_DEFAULT if kind == "python" else NODE_BASE_DEFAULT
    value = os.environ.get(env_name, "").strip()
    if not value:
        return default
    if value not in BASE_IMAGES[kind]:
        allowed = "、".join(sorted(BASE_IMAGES[kind]))
        raise GuardError(f"{env_name} 不在允许列表中: {allowed}")
    return value


def short_image(image: str) -> str:
    return image.removeprefix("sha256:")[:12]


def print_contract(contract: dict) -> str:
    ports = contract["ports"]
    return "\n".join([
        f"项目: {contract['project']}",
        f"工作目录: {contract['working_dir']}",
        f"数据库卷: {contract['volume']}",
        "端口: "
        f"api {ports['api']['host_port']}->{ports['api']['container_port']}, "
        f"db {ports['db']['host_port']}->{ports['db']['container_port']}, "
        f"redis {ports['redis']['host_port']}->{ports['redis']['container_port']}",
        f"网络: {contract['network']}",
        f"db镜像: {contract['db_image']}",
        f"redis镜像: {contract['redis_image']}",
        f"redis卷: {contract['redis_volume']}",
        "不发布: 8000 5432 6379 3000",
        "部署文件: jobs/state/compose.runtime.yml（单独使用，不含 web）",
    ])


def _arg(argv: list[str], name: str) -> str:
    if name not in argv or argv.index(name) + 1 >= len(argv):
        raise GuardError(f"缺少参数 {name}")
    return argv[argv.index(name) + 1]


def _paths(argv: list[str]) -> list[str]:
    values = []
    for index, item in enumerate(argv):
        if item == "--path" and index + 1 < len(argv):
            values.append(argv[index + 1])
    return values


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        raise GuardError("缺少命令")
    command = argv[1]
    if command == "base-image":
        sys.stdout.write(select_base(_arg(argv, "--kind")) + "\n")
        return
    if command == "compose-version":
        sys.stdout.write(describe_compose_version(_arg(argv, "--text")) + "\n")
        return
    if command == "discover":
        contract = discover(
            load_json(Path(_arg(argv, "--inspect"))),
            _arg(argv, "--root"),
            _arg(argv, "--expect-project"),
            _arg(argv, "--expect-volume"),
        )
        sys.stdout.write(json.dumps(contract, ensure_ascii=False, indent=2) + "\n")
        return
    if command == "app-running":
        running = app_running(load_json(Path(_arg(argv, "--inspect"))))
        raise SystemExit(0 if running else 1)
    if command == "write-runtime":
        contract = load_json(Path(_arg(argv, "--contract")))
        output = Path(_arg(argv, "--output"))
        output.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(runtime_yaml(contract))
        return
    if command == "check-config":
        check_config(load_json(Path(_arg(argv, "--config"))), load_json(Path(_arg(argv, "--contract"))))
        return
    if command == "check-rendered":
        check_rendered(Path(_arg(argv, "--config")).read_text(encoding="utf-8"), load_json(Path(_arg(argv, "--contract"))))
        return
    if command == "unchanged":
        assert_unchanged(load_json(Path(_arg(argv, "--contract"))), load_json(Path(_arg(argv, "--inspect"))))
        return
    if command == "health-body":
        assert_healthy(sys.stdin.read())
        return
    if command == "fetch-health":
        sys.stdout.write(fetch_health(_arg(argv, "--url"), float(_arg(argv, "--timeout"))))
        return
    if command == "install-static":
        install_static(Path(_arg(argv, "--src")), _arg(argv, "--dst"))
        return
    if command == "disk":
        assert_disk(_paths(argv), int(_arg(argv, "--min-mb")))
        return
    if command == "assert-repo":
        sys.stdout.write(assert_repo(_arg(argv, "--url")) + "\n")
        return
    if command == "print-field":
        contract = load_json(Path(_arg(argv, "--contract")))
        field = _arg(argv, "--field")
        if field not in {"project", "volume", "redis_volume", "network", "db_container", "redis_container", "db_image", "redis_image", "working_dir"}:
            raise GuardError("字段不允许打印")
        value = str(contract[field])
        if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,200}", value):
            raise GuardError("字段值无法安全打印")
        sys.stdout.write(value + "\n")
        return
    if command == "print-contract":
        sys.stdout.write(print_contract(load_json(Path(_arg(argv, "--contract")))) + "\n")
        return
    raise GuardError(f"未知命令: {command}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except GuardError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
