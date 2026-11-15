"""NAS 脚本共享的 .env 读取与远程路径检查，过程中不打印任何秘密值。"""

from __future__ import annotations

import os
import re
from pathlib import Path


# 主名使用 NAS_ 前缀；回退名兼容既有项目配置。
ENV_FALLBACKS = {
    "NAS_DSM_URL": ("SYNOLOGY_DSM_URL",),
    "NAS_USERNAME": ("SYNOLOGY_USERNAME",),
    "NAS_PASSWORD": ("SYNOLOGY_PASSWORD",),
    "NAS_ALLOWED_ROOT": ("SYNOLOGY_ALLOWED_ROOT",),
}

# 未配置 NAS_DSM_URL 时在报错里提示的入口，不作为隐式默认连接地址。
DSM_URL_HINT = "https://nas.example.com"

# Git Bash 会把 /volume1/... 这类参数改写成 C:/Program Files/Git/volume1/...。
WINDOWS_DRIVE = re.compile(r"^/?[A-Za-z]:[\\/]")


class EnvError(ValueError):
    """表示 .env 缺失、不可读或配置项不完整。"""


def find_env_file(explicit: str | None = None) -> Path | None:
    """定位 .env：显式路径优先，否则从当前工作目录逐级向上查找。"""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise EnvError(f"指定的 .env 不存在：{path}")
        return path
    start = Path.cwd().resolve()
    for directory in (start, *start.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env_file(explicit: str | None = None) -> Path | None:
    """载入简单的 KEY=VALUE 条目并返回实际读取的路径；找不到时返回 None。

    已存在的进程环境变量优先，便于 CI 或临时覆盖。
    """
    path = find_env_file(explicit)
    if path is None:
        return None
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return path


def env_value(name: str) -> str:
    """按主名读取配置，缺失时回退到旧前缀；始终返回字符串。"""
    value = os.getenv(name)
    if not value:
        for fallback in ENV_FALLBACKS.get(name, ()):
            value = os.getenv(fallback)
            if value:
                break
    return (value or "").strip()


def require_credentials(env_file: Path | None) -> tuple[str, str]:
    """返回 NAS 账号密码；缺失时抛出可直接展示给用户的错误。"""
    username = env_value("NAS_USERNAME")
    password = env_value("NAS_PASSWORD")
    if username and password:
        return username, password
    location = (
        str(env_file) if env_file else "项目根目录的 .env（当前工作目录及上级都没找到）"
    )
    raise EnvError(
        f"缺少 NAS_USERNAME 或 NAS_PASSWORD；请在 {location} 中配置，"
        "并确认 .env 已加入 .gitignore。"
    )


def require_dsm_url(explicit: str | None) -> str:
    """返回 DSM 入口地址；缺失时提示可用的入口，不隐式替用户选定 NAS。"""
    dsm_url = (explicit or "").strip() or env_value("NAS_DSM_URL")
    if dsm_url:
        return dsm_url
    raise EnvError(
        "缺少 DSM 地址；请提供 --dsm-url，或在 .env 中配置 "
        f"NAS_DSM_URL（本组常用入口为 {DSM_URL_HINT}）。"
    )


def assert_remote_path_sane(path: str) -> None:
    """拦截被 Git Bash MSYS 路径转换改写过的远程路径。"""
    value = str(path or "").strip()
    if not value:
        raise EnvError("NAS 远程路径不能为空。")
    if "\x00" in value:
        raise EnvError("NAS 远程路径不能包含 NUL 字符。")
    if "://" in value:
        raise EnvError("NAS 远程路径不能是 URL；请传入 /volume1/... 路径。")
    if WINDOWS_DRIVE.match(value):
        raise EnvError(
            f"远程路径含 Windows 盘符，已被 Git Bash 的 MSYS 路径转换改写：{value}。"
            "在 Git Bash 中给命令加 MSYS_NO_PATHCONV=1 前缀，或改用 PowerShell 执行。"
        )


def normalize_remote_path(path: str) -> str:
    """把 /volume1 路径转换成 File Station 路径，并拒绝路径穿越。"""
    assert_remote_path_sane(path)
    parts = [part for part in str(path).strip().replace("\\", "/").split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise EnvError(f"NAS 远程路径不允许包含 . 或 ..：{path}")
    if parts and re.fullmatch(r"volume\d+", parts[0], re.IGNORECASE) and parts[0] != "volume1":
        raise EnvError(f"当前技能只接受 /volume1 或 File Station 共享路径：{path}")
    if parts and parts[0] == "volume1":
        parts = parts[1:]
    return "/" + "/".join(parts) if parts else "/"


def configured_allowed_root(explicit: str | None = None) -> str:
    """读取可选的允许根；脚本兼容旧项目但新接入应始终配置。"""
    return (explicit or "").strip() or env_value("NAS_ALLOWED_ROOT")


def require_remote_path_within(path: str, allowed_root: str = "") -> str:
    """返回 File Station 路径；配置允许根时执行组件边界校验。"""
    normalized = normalize_remote_path(path)
    if not allowed_root:
        return normalized
    root = normalize_remote_path(allowed_root).rstrip("/") or "/"
    if root != "/" and normalized != root and not normalized.startswith(root + "/"):
        raise EnvError(
            f"NAS 远程路径超出允许根目录：{path}；允许根为 {allowed_root}。"
        )
    return normalized
