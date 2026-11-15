"""通过群晖 File Station API 只读统计图片素材数量。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import deque
from pathlib import Path

import requests
import urllib3

from nas_env import (
    EnvError,
    configured_allowed_root,
    load_env_file,
    normalize_remote_path,
    require_credentials,
    require_dsm_url,
    require_remote_path_within,
)


MATERIAL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


class NasError(RuntimeError):
    """表示群晖访问或目录统计失败。"""


def emit(result: str, **detail: object) -> None:
    print(json.dumps({"result": result, **detail}, ensure_ascii=False, indent=2))


class FileStationClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_tls: bool,
        timeout: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.session = requests.Session()
        self.sid: str | None = None

    def request(self, method: str, endpoint: str, **kwargs: object) -> requests.Response:
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{endpoint}",
                verify=self.verify_tls,
                timeout=self.timeout,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            reason = type(error).__name__
            if isinstance(error, requests.exceptions.SSLError):
                error_text = str(error)
                if "CERTIFICATE_VERIFY_FAILED" in error_text:
                    reason = (
                        "TLS 证书校验失败；可信内网自签名 DSM 可使用 --insecure，"
                        "否则应安装并信任 DSM 证书。"
                    )
                else:
                    reason = (
                        "TLS 握手失败或 DSM 主动断开连接；请检查 DSM 地址、"
                        "HTTPS 端口、反向代理和服务端 TLS 配置。"
                    )
            if isinstance(error, requests.HTTPError) and error.response is not None:
                reason = f"HTTP {error.response.status_code}"
            raise NasError(f"DSM 请求失败：{method} {endpoint}，原因：{reason}。") from error

    @staticmethod
    def api_json(response: requests.Response, operation: str) -> dict[str, object]:
        try:
            payload = response.json()
        except ValueError as error:
            raise NasError(f"DSM 在执行 {operation} 时返回了非 JSON 数据。") from error
        if not isinstance(payload, dict):
            raise NasError(f"DSM 在执行 {operation} 时返回了非对象 JSON。")
        if not payload.get("success"):
            raise NasError(f"DSM {operation} 失败：{payload.get('error', {})}")
        return payload

    def login(self) -> None:
        response = self.request(
            "POST",
            "/webapi/auth.cgi",
            data={
                "api": "SYNO.API.Auth",
                "version": "6",
                "method": "login",
                "account": self.username,
                "passwd": self.password,
                "session": "FileStation",
                "format": "sid",
            },
        )
        data = self.api_json(response, "login").get("data", {})
        if not isinstance(data, dict) or not data.get("sid"):
            raise NasError("DSM 登录成功，但没有返回 File Station 会话 ID。")
        self.sid = str(data["sid"])

    def list_directory(self, remote_path: str) -> list[dict[str, object]]:
        if not self.sid:
            raise NasError("File Station 会话尚未完成认证。")

        offset = 0
        page_size = 500
        entries: list[dict[str, object]] = []
        while True:
            response = self.request(
                "GET",
                "/webapi/entry.cgi",
                params={
                    "api": "SYNO.FileStation.List",
                    "version": "2",
                    "method": "list",
                    "folder_path": normalize_remote_path(remote_path),
                    "offset": offset,
                    "limit": page_size,
                    "sort_by": "name",
                    "sort_direction": "asc",
                    "filetype": "all",
                    "additional": json.dumps(
                        ["real_path", "type"], ensure_ascii=False
                    ),
                    "_sid": self.sid,
                },
            )
            data = self.api_json(response, "list").get("data", {})
            if not isinstance(data, dict):
                raise NasError("DSM list 响应缺少 data 对象。")
            page = data.get("files", [])
            if not isinstance(page, list):
                raise NasError("DSM list 响应缺少 files 数组。")
            page_entries = [item for item in page if isinstance(item, dict)]
            entries.extend(page_entries)
            total = data.get("total")
            if not page:
                return entries[:total] if isinstance(total, int) else entries
            if isinstance(total, int) and len(entries) >= total:
                return entries[:total] if isinstance(total, int) else entries
            if len(page) < page_size:
                return entries
            offset += len(page)

    def count_materials(self, remote_path: str) -> int:
        root = normalize_remote_path(remote_path)
        pending = deque([root])
        visited: set[str] = set()
        count = 0
        while pending:
            current = pending.popleft()
            if current in visited:
                continue
            visited.add(current)
            for entry in self.list_directory(current):
                path = entry.get("path")
                name = entry.get("name")
                if not isinstance(path, str) or not isinstance(name, str):
                    continue
                if bool(entry.get("isdir")):
                    pending.append(require_remote_path_within(path, root))
                elif Path(name).suffix.lower() in MATERIAL_EXTENSIONS:
                    count += 1
        return count

    def logout(self) -> None:
        if not self.sid:
            return
        try:
            self.request(
                "POST",
                "/webapi/auth.cgi",
                data={
                    "api": "SYNO.API.Auth",
                    "version": "6",
                    "method": "logout",
                    "session": "FileStation",
                    "_sid": self.sid,
                },
            )
        except NasError:
            pass
        finally:
            self.sid = None
            self.session.close()

    def __enter__(self) -> "FileStationClient":
        self.login()
        return self

    def __exit__(self, *_: object) -> None:
        self.logout()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读统计群晖目录中的图片素材数量。")
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
        help="目录路径；允许传入 /volume1 开头的完整路径。",
    )
    parser.add_argument(
        "--allowed-root",
        help="允许访问的 NAS 根路径；默认读取 NAS_ALLOWED_ROOT。",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="仅在可信内网中接受自签名或不完整证书链。",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="请求超时秒数。")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()

    env_file: Path | None = None
    remote_path = args.remote_path
    allowed_root = ""
    try:
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
            material_count = client.count_materials(remote_path)
        emit(
            "SUCCESS",
            env_file=str(env_file) if env_file else None,
            remote_path=remote_path,
            allowed_root=normalize_remote_path(allowed_root) if allowed_root else None,
            material_count=material_count,
            image_extensions=sorted(MATERIAL_EXTENSIONS),
        )
        return 0
    except (NasError, EnvError, OSError) as error:
        emit("FAILED", remote_path=remote_path, message=str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
