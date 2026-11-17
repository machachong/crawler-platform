#!/usr/bin/env python3
"""Copy a fetched Git tree without deleting server-only files or secrets."""

from __future__ import annotations

import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

PROTECTED_EXACT = {
    ".env",
    "docker-compose.override.yml",
    "jobs/create-admin.sh",
    "jobs/mirrors.env",
    "jobs/preserve.list",
}
REQUIRED_STAGE = (
    "docker-compose.yml",
    "api/Dockerfile",
    "api/scripts/migrate_v2.py",
)


class SyncError(SystemExit):
    pass


def _arg(argv: list[str], name: str) -> str:
    if name not in argv or argv.index(name) + 1 >= len(argv):
        raise SyncError(f"缺少参数 {name}")
    return argv[argv.index(name) + 1]


def _rel(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def should_exclude(relative: str) -> bool:
    if relative in ("", "."):
        return False
    parts = relative.split("/")
    if set(parts) & {".git", "node_modules", "__pycache__", ".venv"}:
        return True
    if len(parts) >= 2 and parts[0] == "jobs" and parts[1] == "state":
        return True
    filename = parts[-1]
    if filename == ".env" or (filename.startswith(".env.") and filename != ".env.example"):
        return True
    if relative == "jobs/mirrors.env" or relative == "web/dist" or relative.startswith("web/dist/"):
        return True
    if filename.endswith(".pyc"):
        return True
    return False


def load_preserve(root: Path) -> set[str]:
    path = root / "jobs/preserve.list"
    if not path.exists():
        return set()
    extra = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.split("#", 1)[0].strip()
        if not item:
            continue
        if item.startswith("/") or ".." in item.split("/"):
            raise SyncError("preserve.list 含有非法路径")
        extra.add(item)
    return extra


def is_protected(relative: str, extra: set[str]) -> bool:
    relative = relative.strip("/")
    if relative in PROTECTED_EXACT or relative in extra:
        return True
    if relative.startswith("jobs/state/"):
        return True
    filename = relative.rsplit("/", 1)[-1]
    if filename == ".env" or (filename.startswith(".env.") and filename != ".env.example"):
        return True
    return False


def safe_join(root: Path, relative: str) -> Path:
    root_resolved = root.resolve()
    current = root_resolved
    for part in relative.split("/"):
        if part in ("", ".", ".."):
            raise SyncError(f"非法路径: {relative}")
        current = current / part
        if current.is_symlink():
            raise SyncError(f"拒绝符号链接: {relative}")
    current.relative_to(root_resolved)
    return current


def list_files(stage: Path) -> list[str]:
    files = []
    for directory, dirnames, filenames in os.walk(stage, followlinks=False):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        parent = Path(directory).relative_to(stage).as_posix()
        if parent == ".":
            parent = ""
        for name in filenames:
            path = Path(directory) / name
            if path.is_symlink():
                raise SyncError(f"上游包含符号链接: {path.name}")
            files.append(_rel(parent, name))
    return sorted(files)


def assert_stage(stage: Path) -> None:
    for relative in REQUIRED_STAGE:
        if not (stage / relative).is_file():
            raise SyncError(f"上游缺少 {relative}")


def read_manifest(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def write_manifest(path: Path, files: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(files) + ("\n" if files else ""))


def sync_tree(stage: Path, root: Path, manifest_path: Path) -> None:
    if root.is_symlink():
        raise SyncError("项目目录不能是符号链接")
    assert_stage(stage)
    extra = load_preserve(root)
    files = list_files(stage)
    for relative in files:
        if is_protected(relative, extra):
            continue
        source = safe_join(stage, relative)
        target = safe_join(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o755)
        data = source.read_bytes()
        temporary = target.with_name(target.name + ".tmp-sync")
        temporary.write_bytes(data)
        os.chmod(temporary, source.stat().st_mode & 0o777)
        os.replace(temporary, target)
    previous = read_manifest(manifest_path)
    current = set(files)
    for relative in sorted(previous - current):
        if is_protected(relative, extra):
            continue
        target = safe_join(root, relative)
        if target.is_file() and not target.is_symlink():
            target.unlink()
            parent = target.parent
            while parent != root.resolve():
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent
    write_manifest(manifest_path, files)


def make_backup(root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz") as archive:
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            parent = Path(directory).relative_to(root).as_posix()
            if parent == ".":
                parent = ""
            dirnames[:] = [name for name in dirnames if not should_exclude(_rel(parent, name))]
            for name in filenames:
                relative = _rel(parent, name)
                if should_exclude(relative):
                    continue
                path = Path(directory) / name
                if path.is_symlink() or not path.is_file():
                    continue
                archive.add(path, arcname=relative, recursive=False)
    os.chmod(output, 0o600)


def prune_backups(directory: Path, keep: int) -> None:
    files = sorted(directory.glob("code-*.tar.gz"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink()


def write_revision(path: Path, sha: str, ref: str, source: str) -> None:
    if not all(char in "0123456789abcdef" for char in sha) or len(sha) != 40:
        raise SyncError("修订号不是 40 位 SHA")
    if "\n" in ref or "\n" in source or "token" in source.lower() or "@" in source:
        raise SyncError("修订记录含有不安全内容")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = f"sha={sha}\nref={ref}\nsource={source}\nupdated_at={stamp}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        raise SyncError("缺少命令")
    command = argv[1]
    if command == "assert-stage":
        assert_stage(Path(_arg(argv, "--stage")))
        return
    if command == "backup":
        make_backup(Path(_arg(argv, "--root")), Path(_arg(argv, "--output")))
        return
    if command == "sync":
        sync_tree(Path(_arg(argv, "--stage")), Path(_arg(argv, "--root")), Path(_arg(argv, "--manifest")))
        return
    if command == "prune":
        prune_backups(Path(_arg(argv, "--dir")), int(_arg(argv, "--keep")))
        return
    if command == "revision":
        write_revision(Path(_arg(argv, "--path")), _arg(argv, "--sha"), _arg(argv, "--ref"), _arg(argv, "--source"))
        return
    raise SyncError(f"未知命令: {command}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except SyncError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
