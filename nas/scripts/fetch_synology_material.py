"""通过群晖 File Station API 下载并校验单个文件。"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import BinaryIO

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


CHUNK_SIZE = 1024 * 1024
SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
}


class NasError(RuntimeError):
    """表示群晖访问或文件校验失败。"""

    pass


def emit(result: str, **detail: object) -> None:
    print(json.dumps({"result": result, **detail}, ensure_ascii=False, indent=2))


def copy_stream(source: BinaryIO, destination: Path) -> Path:
    """写入同目录临时文件；校验通过前不替换最终目标。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    try:
        with temporary.open("wb") as output:
            shutil.copyfileobj(source, output, length=CHUNK_SIZE)
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


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
            if isinstance(error, requests.HTTPError) and error.response is not None:
                reason = f"HTTP {error.response.status_code}"
            raise NasError(
                f"DSM 请求失败：{method} {endpoint}，原因：{reason}。"
            ) from error

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
            raise NasError("DSM 登录成功，但没有返回会话 ID。")
        self.sid = str(data["sid"])

    def download(self, remote_path: str, destination: Path) -> Path:
        if not self.sid:
            raise NasError("File Station 会话尚未完成认证。")
        response = self.request(
            "GET",
            "/webapi/entry.cgi",
            params={
                "api": "SYNO.FileStation.Download",
                "version": "2",
                "method": "download",
                "path": normalize_remote_path(remote_path),
                "mode": "download",
                "_sid": self.sid,
            },
            stream=True,
        )
        try:
            content_type = response.headers.get("Content-Type", "").lower()
            if "application/json" in content_type:
                self.api_json(response, "download")
                raise NasError("DSM 返回了元数据而不是文件内容。")
            return copy_stream(response.raw, destination)
        finally:
            response.close()

    def logout(self) -> None:
        if not self.sid:
            return
        try:
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


def validate_file(path: Path, logical_path: Path | None = None) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size == 0:
        raise NasError("下载文件不存在或内容为空。")

    digest = hashlib.sha256()
    with path.open("rb") as file:
        header = file.read(16)
        digest.update(header)
        for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
            digest.update(chunk)

    logical_path = logical_path or path
    extension = logical_path.suffix.lower()
    signatures = SIGNATURES.get(extension)
    signature_valid = True
    if signatures:
        signature_valid = any(header.startswith(signature) for signature in signatures)
        if extension == ".webp":
            signature_valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
        if not signature_valid:
            raise NasError(f"下载的 {extension} 文件签名无效。")

    return {
        "local_path": str(logical_path.resolve()),
        "filename": logical_path.name,
        "size_bytes": path.stat().st_size,
        "mime_type": mimetypes.guess_type(logical_path.name)[0]
        or "application/octet-stream",
        "signature_valid": signature_valid,
        "sha256": digest.hexdigest(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过群晖 File Station API 下载并校验文件。"
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
        help="NAS 文件路径；允许传入以 /volume1 开头的内部路径。",
    )
    parser.add_argument(
        "--allowed-root",
        help="允许访问的 NAS 根路径；默认读取 NAS_ALLOWED_ROOT。",
    )
    parser.add_argument("--output", type=Path, help="本地输出文件路径。")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="在可信内网中接受群晖自签名或证书链不完整的 HTTPS 证书。",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="请求超时秒数。")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()

    env_file: Path | None = None
    remote_path = args.remote_path
    temporary: Path | None = None
    try:
        env_file = load_env_file(args.env_file)
        allowed_root = configured_allowed_root(args.allowed_root)
        remote_path = require_remote_path_within(args.remote_path, allowed_root)
        remote_name = PurePosixPath(remote_path).name
        if not remote_name:
            raise NasError("下载路径必须指向文件，不能是共享目录根。")
        destination = (
            args.output or Path("nas_downloads") / remote_name
        )
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
            temporary = client.download(remote_path, destination)
        material = validate_file(temporary, destination)
        temporary.replace(destination)
        temporary = None
        emit(
            "SUCCESS",
            env_file=str(env_file) if env_file else None,
            remote_path=remote_path,
            allowed_root=normalize_remote_path(allowed_root) if allowed_root else None,
            material=material,
        )
        return 0
    except (NasError, EnvError, OSError) as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        emit("FAILED", remote_path=remote_path, message=str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
