#!/usr/bin/env python3
"""Allowlisted apt/pip/npm mirrors and bounded apt timeouts for image builds.

Empty mirror settings keep the image defaults (deb.debian.org and pypi.org).
Signature and certificate checks stay enabled.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from urllib.parse import urlsplit

APT_MAIN = {
    "http://deb.debian.org/debian",
    "https://deb.debian.org/debian",
    "https://mirrors.aliyun.com/debian",
    "https://mirrors.cloud.tencent.com/debian",
    "https://mirrors.tuna.tsinghua.edu.cn/debian",
    "https://mirrors.ustc.edu.cn/debian",
}
APT_SECURITY = {
    "http://deb.debian.org/debian-security",
    "https://deb.debian.org/debian-security",
    "https://mirrors.aliyun.com/debian-security",
    "https://mirrors.cloud.tencent.com/debian-security",
    "https://mirrors.tuna.tsinghua.edu.cn/debian-security",
    "https://mirrors.ustc.edu.cn/debian-security",
}
PIP_INDEXES = {
    "https://pypi.org/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://mirrors.cloud.tencent.com/pypi/simple",
    "https://mirrors.ustc.edu.cn/pypi/simple",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
}
NPM_REGISTRIES = {
    "https://registry.npmjs.org",
    "https://registry.npmmirror.com",
    "https://mirrors.cloud.tencent.com/npm",
    "https://mirrors.tuna.tsinghua.edu.cn/npm",
    "https://mirrors.ustc.edu.cn/npm",
}
MIRROR_KEYS = ("APT_MIRROR", "APT_SECURITY_MIRROR", "PIP_INDEX_URL", "NPM_REGISTRY")
OFFICIAL_MAIN = (
    "https://deb.debian.org/debian",
    "http://deb.debian.org/debian",
    "https://deb.debian.org/debian/",
    "http://deb.debian.org/debian/",
)
OFFICIAL_SECURITY = (
    "https://deb.debian.org/debian-security",
    "http://deb.debian.org/debian-security",
    "https://deb.debian.org/debian-security/",
    "http://deb.debian.org/debian-security/",
)


class MirrorError(SystemExit):
    pass


def normalize(url: str, *, https_only: bool) -> str:
    raw = url.strip()
    if not raw or any(ch in raw for ch in "\r\n\t "):
        raise MirrorError("镜像地址为空或含空白")
    parts = urlsplit(raw)
    if parts.username or parts.password or parts.query or parts.fragment:
        raise MirrorError("镜像地址不能带账号、查询参数或片段")
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.path == "":
        raise MirrorError("镜像地址必须是 http(s) URL")
    if https_only and parts.scheme != "https":
        raise MirrorError("该镜像必须使用 https")
    if parts.scheme == "http" and parts.hostname != "deb.debian.org":
        raise MirrorError("除 deb.debian.org 外，镜像必须使用 https")
    path = parts.path.rstrip("/")
    return f"{parts.scheme}://{parts.netloc}{path}"


def check_choice(url: str, allowed: set[str], *, https_only: bool) -> str:
    canonical = normalize(url, https_only=https_only)
    if canonical not in allowed:
        raise MirrorError("镜像不在允许列表中")
    return canonical


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if not path.is_file():
        raise MirrorError("mirrors.env 必须是文件")
    values: dict[str, str] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):]
        if "=" not in stripped:
            raise MirrorError(f"mirrors.env 第 {lineno} 行无法解析")
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key not in MIRROR_KEYS:
            raise MirrorError(f"mirrors.env 含有不允许的变量: {key}")
        if any(ch in value for ch in "\r\n"):
            raise MirrorError("镜像配置含有换行")
        values[key] = value
    apt = values.get("APT_MIRROR", "")
    security = values.get("APT_SECURITY_MIRROR", "")
    if bool(apt) != bool(security):
        raise MirrorError("APT_MIRROR 与 APT_SECURITY_MIRROR 必须同时设置或同时留空")
    if apt:
        values["APT_MIRROR"] = check_choice(apt, APT_MAIN, https_only=False)
    if security:
        values["APT_SECURITY_MIRROR"] = check_choice(security, APT_SECURITY, https_only=False)
    pip = values.get("PIP_INDEX_URL", "")
    if pip:
        values["PIP_INDEX_URL"] = check_choice(pip, PIP_INDEXES, https_only=True)
    npm = values.get("NPM_REGISTRY", "")
    if npm:
        values["NPM_REGISTRY"] = check_choice(npm, NPM_REGISTRIES, https_only=True)
    return {key: values[key] for key in MIRROR_KEYS if values.get(key)}


def shell_exports(path: Path) -> str:
    values = parse_env_file(path)
    return "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items())


def pip_command(index_url: str = "") -> list[str]:
    command = [
        "python3", "-m", "pip", "install",
        "--no-cache-dir",
        "--disable-pip-version-check",
        "--prefer-binary",
        "--timeout", "60",
        "--retries", "3",
    ]
    if index_url:
        command.extend(["--index-url", check_choice(index_url, PIP_INDEXES, https_only=True)])
    command.extend(["-r", "requirements.txt"])
    if any("trusted-host" in part or "fix-missing" in part for part in command):
        raise MirrorError("pip 命令含有被禁止的参数")
    return command


def _source_files(root: Path) -> list[Path]:
    found: list[Path] = []
    listed = root / "etc/apt/sources.list"
    if listed.is_symlink():
        raise MirrorError("拒绝跟随 apt sources.list 符号链接")
    if listed.is_file():
        found.append(listed)
    directory = root / "etc/apt/sources.list.d"
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.is_symlink():
                raise MirrorError(f"拒绝跟随 apt 源符号链接: {path.name}")
            if path.is_file():
                found.append(path)
    if not found:
        raise MirrorError("找不到 apt 源")
    return found


def _parse_os_release(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def apply_apt(root: Path, apt_mirror: str = "", security_mirror: str = "") -> None:
    os_release = root / "etc/os-release"
    if not os_release.is_file():
        raise MirrorError("找不到 /etc/os-release")
    info = _parse_os_release(os_release)
    if info.get("VERSION_CODENAME") != "bookworm" or info.get("VERSION_ID") != "12":
        raise MirrorError("基础系统不是受支持的 Debian 12 (bookworm)")
    if bool(apt_mirror) != bool(security_mirror):
        raise MirrorError("APT 主源和安全源必须同时设置或同时留空")
    if apt_mirror:
        apt_mirror = check_choice(apt_mirror, APT_MAIN, https_only=False)
    if security_mirror:
        security_mirror = check_choice(security_mirror, APT_SECURITY, https_only=False)
    files = _source_files(root)
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    if "bullseye" in combined:
        raise MirrorError("apt 源仍指向 bullseye，拒绝在已停止安全更新的发行版上安装")
    if "bookworm" not in combined:
        raise MirrorError("apt 源没有 bookworm")
    conf = root / "etc/apt/apt.conf.d/99-crawler-timeouts"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(
        'Acquire::Retries "3";\n'
        'Acquire::http::Timeout "30";\n'
        'Acquire::https::Timeout "30";\n'
        'Acquire::ftp::Timeout "30";\n',
        encoding="utf-8",
    )
    if "Check-Valid-Until" in conf.read_text(encoding="utf-8"):
        raise MirrorError("超时配置错误")
    if not apt_mirror:
        return
    for path in files:
        text = path.read_text(encoding="utf-8")
        for old in OFFICIAL_SECURITY:
            text = text.replace(old, security_mirror)
        for old in OFFICIAL_MAIN:
            text = text.replace(old, apt_mirror)
        if "trusted=yes" in text or "allow-insecure=yes" in text:
            raise MirrorError("拒绝关闭 apt 签名检查")
        path.write_text(text, encoding="utf-8")
    rewritten = "\n".join(path.read_text(encoding="utf-8") for path in files)
    if "deb.debian.org" in rewritten:
        raise MirrorError("官方 apt 源没有被完整替换")
    if security_mirror not in rewritten or apt_mirror not in rewritten:
        raise MirrorError("镜像地址没有写入 apt 源")
    if "bullseye" in rewritten or "bookworm" not in rewritten:
        raise MirrorError("替换后的 apt 源不是 bookworm")


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        raise MirrorError("用法: mirrors.py shell-exports|apply-apt|pip-install")
    command = argv[1]
    if command == "shell-exports":
        path = Path(argv[2]) if len(argv) > 2 else Path("/dev/null")
        sys.stdout.write(shell_exports(path))
        if shell_exports(path):
            sys.stdout.write("\n")
        return
    if command == "apply-apt":
        root = Path("/")
        if "--root" in argv:
            root = Path(argv[argv.index("--root") + 1])
        apply_apt(
            root,
            os.environ.get("APT_MIRROR", ""),
            os.environ.get("APT_SECURITY_MIRROR", ""),
        )
        return
    if command == "pip-install":
        os.execvp("python3", pip_command(os.environ.get("PIP_INDEX_URL", "")))
    raise MirrorError(f"未知命令: {command}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except MirrorError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise
