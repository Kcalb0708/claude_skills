"""通过群晖 File Station API 校验并批量上传本地图片素材。"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from pathlib import Path, PurePosixPath

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
MATERIAL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),
}
UPLOAD_ERROR_HINTS = {
    1800: "请求缺少 Content-Length，或实际大小与 Content-Length 不一致。",
    1801: "等待上传数据超时。",
    1802: "最后一个 multipart 部分缺少文件名。",
    1803: "上传连接被取消。",
    1804: "文件过大，无法上传到 FAT 文件系统。",
    1805: "目标文件已存在；确认要覆盖时才加 --overwrite。",
}


class NasError(RuntimeError):
    """表示群晖访问、本地校验或上传失败。"""


def emit(result: str, **detail: object) -> None:
    print(json.dumps({"result": result, **detail}, ensure_ascii=False, indent=2))


def validate_image(path: Path) -> dict[str, object]:
    if not path.is_file() or path.stat().st_size == 0:
        raise NasError(f"本地文件不存在或内容为空：{path}")

    digest = hashlib.sha256()
    with path.open("rb") as file:
        header = file.read(16)
        digest.update(header)
        for chunk in iter(lambda: file.read(CHUNK_SIZE), b""):
            digest.update(chunk)

    extension = path.suffix.lower()
    signatures = SIGNATURES.get(extension)
    signature_valid = True
    if signatures:
        signature_valid = any(header.startswith(signature) for signature in signatures)
        if extension == ".webp":
            signature_valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
        if not signature_valid:
            raise NasError(f"本地 {extension} 文件签名无效：{path.name}")

    return {
        "local_path": str(path.resolve()),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "signature_valid": signature_valid,
        "sha256": digest.hexdigest(),
    }


def collect_local_images(local_dir: Path) -> list[Path]:
    if not local_dir.is_dir():
        raise NasError(f"本地目录不存在：{local_dir}")
    files = [
        path
        for path in local_dir.iterdir()
        if path.is_file() and path.suffix.lower() in MATERIAL_EXTENSIONS
    ]
    if not files:
        raise NasError(f"本地目录没有可上传的图片：{local_dir}")
    return sorted(files, key=lambda path: path.name.casefold())


def remote_file_size(entry: dict[str, object] | None) -> int | None:
    if not entry:
        return None
    additional = entry.get("additional")
    if isinstance(additional, dict) and isinstance(additional.get("size"), int):
        return additional["size"]
    if isinstance(entry.get("size"), int):
        return entry["size"]
    return None


def acl_writable(entry: dict[str, object] | None) -> bool | None:
    if not entry:
        return None
    additional = entry.get("additional")
    if not isinstance(additional, dict):
        return None
    perm = additional.get("perm")
    if not isinstance(perm, dict):
        return None
    acl = perm.get("acl")
    if not isinstance(acl, dict) or "write" not in acl:
        return None
    return bool(acl.get("write")) or bool(acl.get("append"))


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
    def parse_json(response: requests.Response, operation: str) -> dict[str, object]:
        try:
            payload = response.json()
        except ValueError as error:
            raise NasError(f"DSM 在执行 {operation} 时返回了非 JSON 数据。") from error
        if not isinstance(payload, dict):
            raise NasError(f"DSM 在执行 {operation} 时返回了非对象 JSON。")
        return payload

    def api_json(self, response: requests.Response, operation: str) -> dict[str, object]:
        payload = self.parse_json(response, operation)
        if not payload.get("success"):
            error = payload.get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            hint = UPLOAD_ERROR_HINTS.get(code) if isinstance(code, int) else None
            detail = f"{error}" if hint is None else f"{error}；{hint}"
            raise NasError(f"DSM {operation} 失败：{detail}")
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

    def _require_sid(self) -> str:
        if not self.sid:
            raise NasError("File Station 会话尚未完成认证。")
        return self.sid

    def getinfo(self, remote_path: str) -> dict[str, object] | None:
        """读取远程条目；目标不存在时返回 None。"""
        response = self.request(
            "GET",
            "/webapi/entry.cgi",
            params={
                "api": "SYNO.FileStation.List",
                "version": "2",
                "method": "getinfo",
                "path": json.dumps([normalize_remote_path(remote_path)], ensure_ascii=False),
                "additional": json.dumps(
                    ["real_path", "size", "type", "perm"], ensure_ascii=False
                ),
                "_sid": self._require_sid(),
            },
        )
        payload = self.parse_json(response, "getinfo")
        if not payload.get("success"):
            error = payload.get("error", {})
            code = error.get("code") if isinstance(error, dict) else None
            if code == 408:
                return None
            raise NasError(f"DSM getinfo 失败：{error}")
        data = payload.get("data", {})
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, list) or not files or not isinstance(files[0], dict):
            return None
        entry = files[0]
        if entry.get("code") == 408:
            return None
        return entry

    def assert_can_write(self, remote_path: str) -> None:
        normalized = normalize_remote_path(remote_path)
        entry = self.getinfo(normalized)
        checked = normalized
        if entry is None:
            parent = str(PurePosixPath(normalized).parent)
            entry = self.getinfo(parent)
            checked = parent
        if entry is None:
            raise NasError(
                f"目标目录及其父目录都不存在：{normalized}。"
                "请核对目标路径，或先在 DSM 中创建已授权的上级目录。"
            )
        if acl_writable(entry) is False:
            raise NasError(
                f"当前 NAS 账号对目录没有写入权限：{checked}。"
                "请改用具备 File Station 写入权限的账号，或换到可写目录。"
            )

    def ensure_folder(self, remote_path: str) -> bool:
        """确保远程目录存在；返回 True 表示本次新建。"""
        normalized = normalize_remote_path(remote_path)
        existing = self.getinfo(normalized)
        if existing is not None:
            if not bool(existing.get("isdir")):
                raise NasError(f"远程路径已存在且不是目录：{normalized}")
            return False

        parent = str(PurePosixPath(normalized).parent)
        name = PurePosixPath(normalized).name
        if parent == "/" or not name:
            raise NasError(f"不能在共享根路径直接创建该目录：{normalized}")
        response = self.request(
            "POST",
            "/webapi/entry.cgi",
            data={
                "api": "SYNO.FileStation.CreateFolder",
                "version": "2",
                "method": "create",
                "folder_path": json.dumps([parent], ensure_ascii=False),
                "name": json.dumps([name], ensure_ascii=False),
                "force_parent": "true",
                "_sid": self._require_sid(),
            },
        )
        self.api_json(response, "create_folder")
        created = self.getinfo(normalized)
        if created is None or not bool(created.get("isdir")):
            raise NasError(f"已请求创建远程目录，但随后无法读取：{normalized}")
        return True

    def upload(self, local_path: Path, dest_folder: str, overwrite: bool) -> None:
        fields = {
            "api": "SYNO.FileStation.Upload",
            "version": "2",
            "method": "upload",
            "path": normalize_remote_path(dest_folder),
            "create_parents": "true",
        }
        # 省略 overwrite 时 DSM 会以错误 1805 拒绝同名文件；传 "false" 是静默跳过。
        if overwrite:
            fields["overwrite"] = "true"
        with local_path.open("rb") as handle:
            response = self.request(
                "POST",
                "/webapi/entry.cgi",
                params={"_sid": self._require_sid()},
                data=fields,
                files={
                    "file": (
                        local_path.name,
                        handle,
                        mimetypes.guess_type(local_path.name)[0]
                        or "application/octet-stream",
                    )
                },
            )
        self.api_json(response, "upload")

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
    parser = argparse.ArgumentParser(
        description="校验本地图片素材并通过群晖 File Station API 上传。"
    )
    parser.add_argument(
        "--dsm-url",
        help="群晖 DSM 地址；默认读取 .env 的 NAS_DSM_URL，--dry-run 时可省略。",
    )
    parser.add_argument(
        "--env-file",
        help="显式指定 .env 路径；默认从当前工作目录逐级向上查找。",
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        required=True,
        help="待上传图片所在的本地目录；只取该目录下的一层图片文件。",
    )
    parser.add_argument(
        "--remote-path",
        required=True,
        help="目标目录；允许传入 /volume1 开头的完整路径。",
    )
    parser.add_argument(
        "--allowed-root",
        help="允许写入的 NAS 根路径；默认读取 NAS_ALLOWED_ROOT。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖同名远程文件；默认遇到同名文件即中止。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做本地校验并打印上传计划，不连接 DSM，不写入任何文件。",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="仅在可信内网中接受自签名或不完整证书链。",
    )
    parser.add_argument("--timeout", type=float, default=120.0, help="请求超时秒数。")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()

    remote_folder: str | None = None
    uploaded: list[dict[str, object]] = []
    env_file: Path | None = None
    try:
        env_file = load_env_file(args.env_file)
        allowed_root = configured_allowed_root(args.allowed_root)
        if not args.dry_run and not allowed_root:
            raise EnvError(
                "实跑上传必须通过 NAS_ALLOWED_ROOT 或 --allowed-root 限定写入范围。"
            )
        remote_folder = require_remote_path_within(
            normalize_remote_path(args.remote_path),
            allowed_root,
        )
        planned = [
            {
                **validate_image(path),
                "remote_path": str(PurePosixPath(remote_folder) / path.name),
            }
            for path in collect_local_images(args.local_dir)
        ]
        if args.dry_run:
            emit(
                "SUCCESS",
                dry_run=True,
                env_file=str(env_file) if env_file else None,
                local_dir=str(args.local_dir.resolve()),
                remote_folder=remote_folder,
                allowed_root=normalize_remote_path(allowed_root) if allowed_root else None,
                overwrite=args.overwrite,
                file_count=len(planned),
                files=planned,
            )
            return 0

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
            client.assert_can_write(remote_folder)
            folder_created = client.ensure_folder(remote_folder)
            for item in planned:
                remote_path = str(item["remote_path"])
                existed_before = client.getinfo(remote_path) is not None
                if existed_before and not args.overwrite:
                    raise NasError(f"远程文件已存在，未指定 --overwrite：{remote_path}")
                verified_by = "upload_response"
                try:
                    client.upload(
                        Path(str(item["local_path"])),
                        remote_folder,
                        overwrite=args.overwrite,
                    )
                except NasError as upload_error:
                    # 新建上传可能在 DSM 已落盘后才断开；仅在此前不存在且大小吻合时确认。
                    try:
                        remote_after_error = client.getinfo(remote_path)
                    except NasError:
                        raise upload_error
                    if existed_before or remote_file_size(remote_after_error) != item["size_bytes"]:
                        raise upload_error
                    verified_by = "directory_reconciliation"
                remote = client.getinfo(remote_path)
                if remote is None:
                    raise NasError(f"上传后无法读取远程文件：{remote_path}")
                remote_size = remote_file_size(remote)
                if remote_size != item["size_bytes"]:
                    raise NasError(
                        f"上传后大小不一致：{remote_path}，"
                        f"本地 {item['size_bytes']} 字节，远程 {remote_size} 字节。"
                    )
                uploaded.append(
                    {
                        **item,
                        "remote_size_bytes": remote_size,
                        "size_matched": True,
                        "verified_by": verified_by,
                    }
                )

        emit(
            "SUCCESS",
            env_file=str(env_file) if env_file else None,
            local_dir=str(args.local_dir.resolve()),
            remote_folder=remote_folder,
            allowed_root=normalize_remote_path(allowed_root) if allowed_root else None,
            folder_created=folder_created,
            overwrite=args.overwrite,
            file_count=len(uploaded),
            files=uploaded,
        )
        return 0
    except (NasError, EnvError, OSError) as error:
        emit(
            "FAILED",
            remote_folder=remote_folder,
            uploaded_count=len(uploaded),
            uploaded_files=[str(item["filename"]) for item in uploaded],
            message=str(error),
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
