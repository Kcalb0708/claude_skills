"""只读验证群晖登录和目标目录列举权限，不递归、不下载。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import urllib3

from count_synology_materials import FileStationClient, NasError
from nas_env import (
    EnvError,
    configured_allowed_root,
    load_env_file,
    normalize_remote_path,
    require_credentials,
    require_dsm_url,
    require_remote_path_within,
)


def emit(result: str, **detail: object) -> None:
    print(json.dumps({"result": result, **detail}, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只读验证群晖 DSM 登录和指定目录的 File Station 列举权限。"
    )
    parser.add_argument(
        "--dsm-url",
        help="群晖 DSM 地址；默认读取 .env 的 NAS_DSM_URL。",
    )
    parser.add_argument(
        "--env-file",
        help="显式指定 .env 路径；默认从当前工作目录逐级向上查找。",
    )
    parser.add_argument(
        "--remote-path",
        required=True,
        help="要验证的目录；允许传入 /volume1 开头的完整路径。",
    )
    parser.add_argument(
        "--allowed-root",
        help="允许访问的 NAS 根路径；默认读取 NAS_ALLOWED_ROOT。",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=0,
        help="显式输出最多多少个条目名称；默认 0，不显示目录内容名称。",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="仅在可信内网中接受自签名或不完整证书链。",
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="请求超时秒数。")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    env_file: Path | None = None
    remote_path = args.remote_path
    try:
        if args.sample_limit < 0 or args.sample_limit > 100:
            raise EnvError("--sample-limit 必须在 0 到 100 之间。")
        env_file = load_env_file(args.env_file)
        allowed_root = configured_allowed_root(args.allowed_root)
        remote_path = require_remote_path_within(args.remote_path, allowed_root)
        username, password = require_credentials(env_file)
        dsm_url = require_dsm_url(args.dsm_url)
        if args.insecure:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        with FileStationClient(
            dsm_url,
            username,
            password,
            verify_tls=not args.insecure,
            timeout=args.timeout,
        ) as client:
            entries = client.list_directory(remote_path)

        directories = sum(bool(item.get("isdir")) for item in entries)
        files = len(entries) - directories
        sample = [
            str(item.get("name") or "")
            for item in entries[: args.sample_limit]
            if str(item.get("name") or "")
        ]
        emit(
            "SUCCESS",
            operation="read_only_probe",
            env_file=str(env_file) if env_file else None,
            remote_path=remote_path,
            allowed_root=normalize_remote_path(allowed_root) if allowed_root else None,
            entry_count=len(entries),
            directory_count=directories,
            file_count=files,
            sampled_names=sample,
        )
        return 0
    except (NasError, EnvError, OSError) as error:
        emit("FAILED", operation="read_only_probe", remote_path=remote_path, message=str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
