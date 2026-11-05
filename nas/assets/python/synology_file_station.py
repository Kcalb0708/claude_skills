"""可复制到业务项目中的群晖 File Station 生产适配器。

本模块只依赖 ``requests``，不会读取 skill 目录中的任何运行时资源。目标项目应
把它复制到自己的包内，再按项目的配置、日志和依赖注入约定做薄适配。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import posixpath
import re
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterator,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)
from urllib.parse import urlsplit

import requests

ApiCode = Union[int, str, None]
_WINDOWS_DRIVE = re.compile(r"^/?[A-Za-z]:[\\/]")
_RANGE = re.compile(r"^bytes=(?:(\d+)-(\d*)|-(\d+))$")


class NasError(RuntimeError):
    """所有可预期 NAS 失败的基类。"""


class NasConfigurationError(NasError):
    """本地配置缺失或不合法；修正配置前不应重试。"""


class NasPathError(NasError):
    """路径非法、越界或文件名不是单个路径组件。"""


class NasProtocolError(NasError):
    """DSM 响应形状不符合已验证的 File Station 契约。"""


class NasNotFoundError(NasError):
    """经过读回确认的源或目录不存在。"""


class NasConflictError(NasError):
    """目标已存在或源/目标状态与操作前置条件冲突。"""


class NasApiError(NasError):
    """DSM 已返回可解析的业务失败；消息不包含 URL、表单或秘密。"""

    def __init__(
        self,
        api: str,
        method: str,
        code: ApiCode,
        *,
        http_status: Optional[int] = None,
        detail: str = "",
    ) -> None:
        self.api = api
        self.method = method
        self.code = code
        self.http_status = http_status
        suffix = f"，{detail}" if detail else ""
        super().__init__(f"NAS API {api}.{method} 失败，业务错误码 {code}{suffix}")


class NasAuthenticationError(NasApiError):
    """登录失败、File Station 登录权限不足或需要二次验证。"""


class NasPermissionError(NasApiError):
    """已登录账号没有目标操作权限。"""


class NasTransportError(NasError):
    """DNS、TLS、连接、HTTP 或读取失败；不保留 requests 原始消息。"""

    def __init__(
        self,
        api: str,
        method: str,
        error_kind: str,
        *,
        http_status: Optional[int] = None,
    ) -> None:
        self.api = api
        self.method = method
        self.error_kind = error_kind
        self.http_status = http_status
        status = f"，HTTP {http_status}" if http_status is not None else ""
        super().__init__(f"NAS 传输失败：{api}.{method}，{error_kind}{status}")


class NasOutcomeUnknown(NasError):
    """写请求可能已经生效，必须先对账，禁止直接重放。"""

    def __init__(
        self,
        operation: str,
        source_path: str,
        target_path: str,
        observed_state: str,
    ) -> None:
        self.operation = operation
        self.source_path = source_path
        self.target_path = target_path
        self.observed_state = observed_state
        super().__init__(
            f"NAS {operation} 结果未知，需要对账；当前状态：{observed_state}"
        )


def _parse_bool(name: str, value: str, default: bool) -> bool:
    clean = str(value or "").strip().lower()
    if not clean:
        return default
    if clean in {"1", "true", "yes", "on"}:
        return True
    if clean in {"0", "false", "no", "off"}:
        return False
    raise NasConfigurationError(f"配置项 {name} 必须是 true 或 false")


def _parse_positive_float(name: str, value: str, default: float) -> float:
    clean = str(value or "").strip()
    if not clean:
        return default
    try:
        parsed = float(clean)
    except ValueError:
        raise NasConfigurationError(f"配置项 {name} 必须是正数") from None
    if parsed <= 0:
        raise NasConfigurationError(f"配置项 {name} 必须是正数")
    return parsed


def _parse_page_size(value: str, default: int = 500) -> int:
    clean = str(value or "").strip()
    if not clean:
        return default
    try:
        parsed = int(clean)
    except ValueError:
        raise NasConfigurationError("配置项 NAS_LIST_PAGE_SIZE 必须是整数") from None
    if not 1 <= parsed <= 1000:
        raise NasConfigurationError("配置项 NAS_LIST_PAGE_SIZE 必须介于 1 和 1000")
    return parsed


def normalize_volume_path(raw: str) -> str:
    """规范化业务路径，但绝不通过折叠 ``..`` 来掩盖越界输入。"""

    value = str(raw or "").strip()
    if not value or "\x00" in value or "://" in value or _WINDOWS_DRIVE.match(value):
        raise NasPathError("NAS 路径为空、是 URL、含 NUL 或被改写成 Windows 路径")
    value = value.replace("\\", "/")
    parts = [part for part in value.split("/") if part]
    if not parts or parts[0] != "volume1":
        raise NasPathError("NAS 业务路径必须以 /volume1 开头")
    if any(part in {".", ".."} for part in parts):
        raise NasPathError("NAS 路径不能包含 . 或 ..")
    return "/" + "/".join(parts)


def require_within(raw: str, allowed_root: str) -> str:
    path = normalize_volume_path(raw)
    root = normalize_volume_path(allowed_root).rstrip("/")
    if path != root and not path.startswith(root + "/"):
        raise NasPathError("NAS 路径超出 NAS_ALLOWED_ROOT")
    return path


def validate_component(raw: str, *, label: str = "路径组件") -> str:
    value = str(raw or "")
    if (
        not value
        or value != value.strip()
        or "\x00" in value
        or "/" in value
        or "\\" in value
        or value in {".", ".."}
    ):
        raise NasPathError(f"{label} 必须是非空的单个路径组件")
    return value


def join_volume_path(parent: str, *components: str) -> str:
    result = normalize_volume_path(parent).rstrip("/")
    for component in components:
        result += "/" + validate_component(component)
    return normalize_volume_path(result)


def file_station_path(volume_path: str) -> str:
    normalized = normalize_volume_path(volume_path)
    return normalized[len("/volume1") :] or "/"


def volume_path_from_file_station(path: str) -> str:
    value = str(path or "").replace("\\", "/")
    if value == "/volume1" or value.startswith("/volume1/"):
        return normalize_volume_path(value)
    return normalize_volume_path("/volume1/" + value.lstrip("/"))


@dataclass(frozen=True)
class SynologyConfig:
    dsm_url: str
    username: str
    password: str = field(repr=False)
    allowed_root: str
    verify_tls: bool = True
    connect_timeout: float = 15.0
    read_timeout: float = 60.0
    transfer_timeout: float = 180.0
    page_size: int = 500

    def __post_init__(self) -> None:
        base_url = str(self.dsm_url or "").strip().rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise NasConfigurationError("NAS_DSM_URL 必须是完整的 HTTP(S) 入口")
        if parsed.query or parsed.fragment:
            raise NasConfigurationError("NAS_DSM_URL 不能包含 query 或 fragment")
        if "/webapi" in parsed.path.rstrip("/").lower():
            raise NasConfigurationError("NAS_DSM_URL 只填写 DSM 入口，不包含 /webapi")
        if not str(self.username or "").strip():
            raise NasConfigurationError("缺少配置项 NAS_USERNAME")
        if not str(self.password or ""):
            raise NasConfigurationError("缺少配置项 NAS_PASSWORD")
        root = normalize_volume_path(self.allowed_root)
        if len(root.strip("/").split("/")) < 2:
            raise NasConfigurationError("NAS_ALLOWED_ROOT 必须至少包含共享目录")
        if (
            self.connect_timeout <= 0
            or self.read_timeout <= 0
            or self.transfer_timeout <= 0
        ):
            raise NasConfigurationError("NAS timeout 必须是正数")
        if not 1 <= int(self.page_size) <= 1000:
            raise NasConfigurationError("NAS page_size 必须介于 1 和 1000")
        object.__setattr__(self, "dsm_url", base_url)
        object.__setattr__(self, "username", str(self.username).strip())
        object.__setattr__(self, "allowed_root", root)
        object.__setattr__(self, "page_size", int(self.page_size))

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "SynologyConfig":
        source = os.environ if environ is None else environ
        required = ("NAS_DSM_URL", "NAS_USERNAME", "NAS_PASSWORD", "NAS_ALLOWED_ROOT")
        missing = [name for name in required if not str(source.get(name, "")).strip()]
        if missing:
            raise NasConfigurationError("缺少 NAS 配置项：" + ", ".join(missing))
        return cls(
            dsm_url=str(source["NAS_DSM_URL"]),
            username=str(source["NAS_USERNAME"]),
            password=str(source["NAS_PASSWORD"]),
            allowed_root=str(source["NAS_ALLOWED_ROOT"]),
            verify_tls=_parse_bool(
                "NAS_VERIFY_TLS", str(source.get("NAS_VERIFY_TLS", "")), True
            ),
            connect_timeout=_parse_positive_float(
                "NAS_CONNECT_TIMEOUT", str(source.get("NAS_CONNECT_TIMEOUT", "")), 15.0
            ),
            read_timeout=_parse_positive_float(
                "NAS_READ_TIMEOUT", str(source.get("NAS_READ_TIMEOUT", "")), 60.0
            ),
            transfer_timeout=_parse_positive_float(
                "NAS_TRANSFER_TIMEOUT",
                str(source.get("NAS_TRANSFER_TIMEOUT", "")),
                180.0,
            ),
            page_size=_parse_page_size(str(source.get("NAS_LIST_PAGE_SIZE", ""))),
        )


@dataclass(frozen=True)
class RemoteEntry:
    path: str
    name: str
    is_dir: bool
    size: Optional[int]
    modified_at: Optional[int]


def select_entries(
    entries: Sequence[RemoteEntry],
    *,
    kind: str = "all",
    suffixes: Sequence[str] = (),
    name_contains: str = "",
    min_size: Optional[int] = None,
    max_size: Optional[int] = None,
) -> Sequence[RemoteEntry]:
    """从标准化条目中按类型、扩展名、名称和大小提取所需结果。"""

    normalized_kind = str(kind or "all").strip().lower()
    if normalized_kind not in {"all", "file", "directory"}:
        raise ValueError("kind 只能是 all、file 或 directory")
    if min_size is not None and min_size < 0:
        raise ValueError("min_size 不能小于 0")
    if max_size is not None and max_size < 0:
        raise ValueError("max_size 不能小于 0")
    if min_size is not None and max_size is not None and min_size > max_size:
        raise ValueError("min_size 不能大于 max_size")

    normalized_suffixes = {
        "." + str(value).strip().lower().lstrip(".")
        for value in suffixes
        if str(value).strip()
    }
    needle = str(name_contains or "").casefold()
    selected = []
    for entry in entries:
        if normalized_kind == "file" and entry.is_dir:
            continue
        if normalized_kind == "directory" and not entry.is_dir:
            continue
        if normalized_suffixes:
            suffix = posixpath.splitext(entry.name)[1].lower()
            if entry.is_dir or suffix not in normalized_suffixes:
                continue
        if needle and needle not in entry.name.casefold():
            continue
        if min_size is not None and (entry.size is None or entry.size < min_size):
            continue
        if max_size is not None and (entry.size is None or entry.size > max_size):
            continue
        selected.append(entry)
    return tuple(selected)


@dataclass(frozen=True)
class DownloadReceipt:
    remote_path: str
    local_path: str
    size: int
    mime_type: str
    sha256: str
    verified_by: str


@dataclass(frozen=True)
class UploadReceipt:
    remote_path: str
    local_path: str
    size: int
    mime_type: str
    sha256: str
    verified_by: str


@dataclass(frozen=True)
class MutationReceipt:
    operation: str
    source_path: str
    target_path: str
    verified_by: str


class DownloadStream:
    """显式拥有 response；路由取消、未消费和异常分支都应调用 ``close``。"""

    def __init__(self, response: Any, remote_path: str) -> None:
        self._response = response
        self.remote_path = remote_path
        self.closed = False

    @property
    def status_code(self) -> int:
        return int(self._response.status_code)

    @property
    def headers(self) -> Mapping[str, str]:
        return self._response.headers

    def iter_bytes(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        if self.closed:
            raise NasProtocolError("NAS 下载流已经关闭")
        try:
            for chunk in self._response.iter_content(chunk_size=chunk_size):
                if chunk:
                    yield bytes(chunk)
        except requests.RequestException as exc:
            self.close()
            raise NasTransportError(
                "SYNO.FileStation.Download", "download", type(exc).__name__
            ) from None

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._response.close()

    def __enter__(self) -> "DownloadStream":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def _safe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_range(value: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        return ""
    match = _RANGE.fullmatch(clean)
    if not match:
        raise NasPathError("Range 只允许一个 bytes=start-end 或 bytes=-suffix 区间")
    start, end, suffix = match.groups()
    if suffix is not None and int(suffix) <= 0:
        raise NasPathError("Range suffix 必须大于 0")
    if start is not None and end and int(end) < int(start):
        raise NasPathError("Range 结束位置不能小于开始位置")
    return clean


def _verify_media_signature(path: Path, expected_suffix: str) -> None:
    suffix = expected_suffix.lower()
    with path.open("rb") as handle:
        head = handle.read(32)
    valid = {
        ".jpg": head.startswith(b"\xff\xd8\xff"),
        ".jpeg": head.startswith(b"\xff\xd8\xff"),
        ".png": head.startswith(b"\x89PNG\r\n\x1a\n"),
        ".gif": head.startswith((b"GIF87a", b"GIF89a")),
        ".webp": len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP",
        ".mp4": len(head) >= 12 and head[4:8] == b"ftyp",
        ".mov": len(head) >= 12 and head[4:8] == b"ftyp",
    }
    if suffix not in valid:
        raise NasProtocolError(f"不支持校验的媒体扩展名：{suffix or '<无>'}")
    if not valid[suffix]:
        raise NasProtocolError(f"下载内容与媒体扩展名 {suffix} 不匹配")


class SynologyFileStation:
    """集中管理认证、路径沙箱、请求契约、流和写后对账。"""

    def __init__(self, config: SynologyConfig, session: Optional[Any] = None) -> None:
        self.config = config
        self.session = session if session is not None else requests.Session()
        self.session.verify = config.verify_tls
        self.sid = ""
        self._closed = False

    def __enter__(self) -> "SynologyFileStation":
        self.login()
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def _require_open(self) -> None:
        if self._closed:
            raise NasProtocolError("NAS client 已关闭")

    def _require_login(self) -> None:
        self._require_open()
        if not self.sid:
            raise NasAuthenticationError(
                "SYNO.API.Auth", "session", "missing_sid", detail="尚未登录"
            )

    @staticmethod
    def _error_from_response(
        api: str,
        method: str,
        code: ApiCode,
        http_status: Optional[int],
    ) -> NasApiError:
        if api == "SYNO.API.Auth":
            hints = {
                400: "账号不存在或密码错误",
                401: "账号已停用",
                402: "没有登录 File Station 的权限",
                403: "需要二次验证",
                404: "二次验证码失败",
            }
            return NasAuthenticationError(
                api, method, code, http_status=http_status, detail=hints.get(code, "")
            )
        if code == 403:
            return NasPermissionError(
                api,
                method,
                code,
                http_status=http_status,
                detail="账号没有目标操作权限",
            )
        return NasApiError(api, method, code, http_status=http_status)

    def _post_json(
        self,
        endpoint: str,
        data: Dict[str, Any],
        *,
        files: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[Tuple[float, float]] = None,
    ) -> Dict[str, Any]:
        self._require_open()
        api = str(data.get("api") or "unknown")
        method = str(data.get("method") or "unknown")
        response: Optional[Any] = None
        try:
            response = self.session.post(
                self.config.dsm_url + endpoint,
                data=data,
                files=files,
                params=params,
                timeout=timeout
                or (self.config.connect_timeout, self.config.read_timeout),
            )
        except requests.RequestException as exc:
            raise NasTransportError(api, method, type(exc).__name__) from None

        status = _safe_int(getattr(response, "status_code", None))
        try:
            body = response.json()
        except (TypeError, ValueError):
            raise NasProtocolError(
                f"NAS API {api}.{method} 返回非 JSON 响应，HTTP {status}"
            ) from None
        finally:
            response.close()

        if not isinstance(body, dict):
            raise NasProtocolError(f"NAS API {api}.{method} 返回的 JSON 不是对象")
        if status is None or not 200 <= status < 300 or body.get("success") is not True:
            error = body.get("error") if isinstance(body.get("error"), dict) else {}
            code: ApiCode = error.get("code") if error else status
            raise self._error_from_response(api, method, code, status)
        return body

    def login(self) -> None:
        self._require_open()
        if self.sid:
            return
        body = self._post_json(
            "/webapi/auth.cgi",
            {
                "api": "SYNO.API.Auth",
                "version": "6",
                "method": "login",
                "account": self.config.username,
                "passwd": self.config.password,
                "session": "FileStation",
                "format": "sid",
            },
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        sid = str(data.get("sid") or "")
        if not sid:
            raise NasProtocolError("NAS 登录响应缺少 data.sid")
        self.sid = sid

    def logout(self) -> None:
        if self._closed or not self.sid:
            return
        sid = self.sid
        self.sid = ""
        with suppress(NasError):
            self._post_json(
                "/webapi/auth.cgi",
                {
                    "api": "SYNO.API.Auth",
                    "version": "6",
                    "method": "logout",
                    "session": "FileStation",
                    "_sid": sid,
                },
            )
        # 注销失败不掩盖已经完成的业务操作；close 仍会释放本地连接池。

    def close(self) -> None:
        if self._closed:
            return
        self.logout()
        self._closed = True
        self.sid = ""
        self.session.close()

    def _entry_from_payload(
        self, raw: Mapping[str, Any], requested_directory: str = ""
    ) -> RemoteEntry:
        name = validate_component(str(raw.get("name") or ""), label="NAS 返回的文件名")
        raw_path = str(raw.get("path") or "")
        if raw_path:
            path = volume_path_from_file_station(raw_path)
            if posixpath.basename(path) != name and requested_directory:
                path = join_volume_path(requested_directory, name)
        elif requested_directory:
            path = join_volume_path(requested_directory, name)
        else:
            raise NasProtocolError("NAS 条目缺少 path")
        path = require_within(path, self.config.allowed_root)
        additional = (
            raw.get("additional") if isinstance(raw.get("additional"), dict) else {}
        )
        time_data = (
            additional.get("time") if isinstance(additional.get("time"), dict) else {}
        )
        size = _safe_int(raw.get("size"))
        if size is None:
            size = _safe_int(additional.get("size"))
        modified_at = _safe_int(time_data.get("mtime"))
        if modified_at is None:
            modified_at = _safe_int(raw.get("mtime"))
        is_dir_value = raw.get("isdir")
        if isinstance(is_dir_value, bool):
            is_dir = is_dir_value
        elif is_dir_value in {0, 1, "0", "1", "false", "true"}:
            is_dir = str(is_dir_value).lower() in {"1", "true"}
        else:
            raise NasProtocolError("NAS 条目的 isdir 不是布尔值")
        return RemoteEntry(
            path=path,
            name=name,
            is_dir=is_dir,
            size=size,
            modified_at=modified_at,
        )

    def list_entries(self, volume_directory: str) -> Sequence[RemoteEntry]:
        self._require_login()
        directory = require_within(volume_directory, self.config.allowed_root)
        offset = 0
        result = []
        previous_signature: Optional[Tuple[str, ...]] = None
        for _page_number in range(10000):
            body = self._post_json(
                "/webapi/entry.cgi",
                {
                    "api": "SYNO.FileStation.List",
                    "version": "2",
                    "method": "list",
                    "folder_path": file_station_path(directory),
                    "additional": json.dumps(
                        ["real_path", "size", "time", "type"], ensure_ascii=False
                    ),
                    "offset": str(offset),
                    "limit": str(self.config.page_size),
                    "sort_by": "name",
                    "sort_direction": "asc",
                    "_sid": self.sid,
                },
            )
            data = body.get("data") if isinstance(body.get("data"), dict) else None
            if data is None or not isinstance(data.get("files", []), list):
                raise NasProtocolError("NAS List 响应缺少 data.files 数组")
            files = data.get("files", [])
            page = [
                self._entry_from_payload(item, directory)
                for item in files
                if isinstance(item, dict)
            ]
            if len(page) != len(files):
                raise NasProtocolError("NAS List 的 files 包含非对象条目")
            signature = tuple(entry.path for entry in page)
            if page and signature == previous_signature:
                raise NasProtocolError("NAS List 返回了重复页面，已停止分页")
            previous_signature = signature
            result.extend(page)
            offset += len(page)

            total_value = data.get("total")
            total: Optional[int] = None
            if total_value not in {None, ""}:
                total = _safe_int(total_value)
                if total is None or total < 0 or total < offset:
                    raise NasProtocolError("NAS List 返回了无效 total")
            if not page or len(page) < self.config.page_size:
                return tuple(result)
            if total is not None and offset >= total:
                return tuple(result)
        raise NasProtocolError("NAS List 超过安全分页上限")

    def list_files(
        self,
        volume_directory: str,
        *,
        suffixes: Sequence[str] = (),
        name_contains: str = "",
        min_size: Optional[int] = None,
        max_size: Optional[int] = None,
    ) -> Sequence[RemoteEntry]:
        """列出目录并只返回符合条件的文件，保持 NAS 的稳定名称顺序。"""

        return select_entries(
            self.list_entries(volume_directory),
            kind="file",
            suffixes=suffixes,
            name_contains=name_contains,
            min_size=min_size,
            max_size=max_size,
        )

    def stat(self, volume_path: str) -> Optional[RemoteEntry]:
        self._require_login()
        path = require_within(volume_path, self.config.allowed_root)
        try:
            body = self._post_json(
                "/webapi/entry.cgi",
                {
                    "api": "SYNO.FileStation.List",
                    "version": "2",
                    "method": "getinfo",
                    "path": json.dumps([file_station_path(path)], ensure_ascii=False),
                    "additional": json.dumps(
                        ["real_path", "size", "time", "type", "perm"],
                        ensure_ascii=False,
                    ),
                    "_sid": self.sid,
                },
            )
        except NasApiError as exc:
            if exc.code == 408:
                return None
            raise
        data = body.get("data") if isinstance(body.get("data"), dict) else None
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, list):
            raise NasProtocolError("NAS getinfo 响应缺少 data.files 数组")
        if not files:
            return None
        if not isinstance(files[0], dict):
            raise NasProtocolError("NAS getinfo 返回的首个条目不是对象")
        return self._entry_from_payload(files[0], posixpath.dirname(path))

    def _create_one_folder(self, parent: str, name: str) -> None:
        parent = require_within(parent, self.config.allowed_root)
        component = validate_component(name, label="目录名")
        target = join_volume_path(parent, component)
        try:
            self._post_json(
                "/webapi/entry.cgi",
                {
                    "api": "SYNO.FileStation.CreateFolder",
                    "version": "2",
                    "method": "create",
                    "folder_path": json.dumps(
                        [file_station_path(parent)], ensure_ascii=False
                    ),
                    "name": json.dumps([component], ensure_ascii=False),
                    "force_parent": "false",
                    "_sid": self.sid,
                },
            )
        except NasError as request_error:
            entry = self.stat(target)
            if entry is not None and entry.is_dir:
                return
            raise request_error
        entry = self.stat(target)
        if entry is None or not entry.is_dir:
            raise NasOutcomeUnknown("create-folder", parent, target, "目标目录未确认")

    def ensure_directory(self, volume_directory: str) -> str:
        self._require_login()
        directory = require_within(volume_directory, self.config.allowed_root)
        root = self.config.allowed_root
        root_entry = self.stat(root)
        if root_entry is None or not root_entry.is_dir:
            raise NasNotFoundError("NAS_ALLOWED_ROOT 不存在或不是目录")
        if directory == root:
            return root
        current = root
        relative = directory[len(root) :].strip("/")
        for component in relative.split("/"):
            candidate = join_volume_path(current, component)
            entry = self.stat(candidate)
            if entry is None:
                self._create_one_folder(current, component)
            elif not entry.is_dir:
                raise NasConflictError("NAS 路径已存在但不是目录")
            current = candidate
        return current

    def open_download(self, volume_path: str, range_header: str = "") -> DownloadStream:
        self._require_login()
        path = require_within(volume_path, self.config.allowed_root)
        range_value = _validate_range(range_header)
        headers = {"Range": range_value} if range_value else {}
        response: Optional[Any] = None
        try:
            response = self.session.get(
                self.config.dsm_url + "/webapi/entry.cgi",
                params={
                    "api": "SYNO.FileStation.Download",
                    "version": "2",
                    "method": "download",
                    "path": file_station_path(path),
                    "mode": "download",
                    "_sid": self.sid,
                },
                headers=headers,
                stream=True,
                timeout=(self.config.connect_timeout, self.config.transfer_timeout),
            )
        except requests.RequestException as exc:
            raise NasTransportError(
                "SYNO.FileStation.Download", "download", type(exc).__name__
            ) from None
        status = _safe_int(getattr(response, "status_code", None))
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if status not in {200, 206} or "application/json" in content_type:
            code: ApiCode = status
            try:
                payload = response.json()
                if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                    code = payload["error"].get("code", code)
            except (TypeError, ValueError):
                pass
            finally:
                response.close()
            raise self._error_from_response(
                "SYNO.FileStation.Download", "download", code, status
            )
        return DownloadStream(response, path)

    def download_to_file(
        self,
        volume_path: str,
        local_path: Path,
        *,
        overwrite: bool = False,
        expected_size: Optional[int] = None,
        expected_sha256: str = "",
        verify_media: bool = False,
    ) -> DownloadReceipt:
        remote = require_within(volume_path, self.config.allowed_root)
        destination = Path(local_path)
        if destination.exists() and not overwrite:
            raise NasConflictError("本地下载目标已存在，拒绝覆盖")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.part"
        )
        mime_type = "application/octet-stream"
        try:
            with self.open_download(remote) as stream:
                mime_type = str(
                    stream.headers.get("Content-Type") or "application/octet-stream"
                ).split(";", 1)[0]
                content_length = _safe_int(stream.headers.get("Content-Length"))
                with temporary.open("xb") as handle:
                    for chunk in stream.iter_bytes():
                        handle.write(chunk)
            size = temporary.stat().st_size
            if size <= 0:
                raise NasProtocolError("NAS 下载文件为空")
            if content_length is not None and size != content_length:
                raise NasProtocolError("NAS 下载大小与 Content-Length 不一致")
            if expected_size is not None and size != expected_size:
                raise NasProtocolError("NAS 下载大小与预期不一致")
            digest = _sha256(temporary)
            if expected_sha256 and digest.lower() != expected_sha256.strip().lower():
                raise NasProtocolError("NAS 下载 SHA-256 与预期不一致")
            if verify_media:
                _verify_media_signature(temporary, destination.suffix)
            os.replace(temporary, destination)
            return DownloadReceipt(
                remote_path=remote,
                local_path=str(destination.resolve()),
                size=size,
                mime_type=mime_type,
                sha256=digest,
                verified_by="stream+size+sha256"
                + ("+signature" if verify_media else ""),
            )
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)

    def remote_sha256(self, volume_path: str) -> Tuple[int, str]:
        """流式回读远程文件，用于高完整性上传验收和定期抽检。"""

        remote = require_within(volume_path, self.config.allowed_root)
        digest = hashlib.sha256()
        size = 0
        with self.open_download(remote) as stream:
            for chunk in stream.iter_bytes():
                size += len(chunk)
                digest.update(chunk)
        return size, digest.hexdigest()

    def upload(
        self,
        local_path: Path,
        volume_directory: str,
        *,
        remote_name: str = "",
        overwrite: bool = False,
        create_directories: bool = False,
        verify_content: bool = False,
    ) -> UploadReceipt:
        self._require_login()
        source = Path(local_path)
        if not source.is_file() or source.stat().st_size <= 0:
            raise NasNotFoundError("上传源必须是存在且非空的本地文件")
        directory = require_within(volume_directory, self.config.allowed_root)
        if create_directories:
            self.ensure_directory(directory)
        else:
            directory_entry = self.stat(directory)
            if directory_entry is None or not directory_entry.is_dir:
                raise NasNotFoundError("NAS 上传目标目录不存在")
        name = validate_component(remote_name or source.name, label="远程文件名")
        target = join_volume_path(directory, name)
        before = self.stat(target)
        if before is not None and not overwrite:
            raise NasConflictError("NAS 上传目标已存在，拒绝覆盖")

        size = source.stat().st_size
        digest = _sha256(source)
        mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        data = {
            "api": "SYNO.FileStation.Upload",
            "version": "2",
            "method": "upload",
            "path": file_station_path(directory),
            "create_parents": "false",
        }
        if overwrite:
            data["overwrite"] = "true"
        try:
            with source.open("rb") as handle:
                self._post_json(
                    "/webapi/entry.cgi",
                    data,
                    files={"file": (name, handle, mime_type)},
                    params={"_sid": self.sid},
                    timeout=(self.config.connect_timeout, self.config.transfer_timeout),
                )
            verification = "upload_response+stat_size"
        except NasError as request_error:
            reconciled = self.stat(target)
            if (
                before is None
                and reconciled is not None
                and not reconciled.is_dir
                and reconciled.size == size
            ):
                verification = "directory_reconciliation+stat_size"
            elif isinstance(request_error, NasTransportError) or before is not None:
                state = "目标不存在" if reconciled is None else "目标大小或类型不匹配"
                raise NasOutcomeUnknown("upload", directory, target, state) from None
            else:
                raise
        else:
            reconciled = self.stat(target)
            if reconciled is None or reconciled.is_dir or reconciled.size != size:
                raise NasOutcomeUnknown(
                    "upload", directory, target, "成功响应后目标大小或类型未确认"
                )
        if verify_content:
            remote_size, remote_digest = self.remote_sha256(target)
            if remote_size != size or remote_digest.lower() != digest.lower():
                raise NasOutcomeUnknown(
                    "upload", directory, target, "远程回读内容与本地源不一致"
                )
            verification += "+sha256_readback"
        return UploadReceipt(
            remote_path=target,
            local_path=str(source.resolve()),
            size=size,
            mime_type=mime_type,
            sha256=digest,
            verified_by=verification,
        )

    def _mutation_state(self, source: str, target: str) -> Tuple[str, bool, bool]:
        source_exists = self.stat(source) is not None
        target_exists = self.stat(target) is not None
        if source_exists and not target_exists:
            return "pending", source_exists, target_exists
        if not source_exists and target_exists:
            return "completed", source_exists, target_exists
        if source_exists and target_exists:
            return "both_source_and_target_exist", source_exists, target_exists
        return "both_source_and_target_missing", source_exists, target_exists

    def _preflight_mutation(self, operation: str, source: str, target: str) -> str:
        state, _, _ = self._mutation_state(source, target)
        if state == "completed":
            return "already_completed"
        if state == "pending":
            return "ready"
        if state == "both_source_and_target_exist":
            raise NasConflictError(f"NAS {operation} 目标已存在，拒绝覆盖")
        raise NasNotFoundError(f"NAS {operation} 来源和目标都不存在")

    def _reconcile_mutation(
        self,
        operation: str,
        source: str,
        target: str,
        *,
        attempts: int = 3,
        delay: float = 0.25,
    ) -> str:
        state = "unknown"
        for attempt in range(attempts):
            state, _, _ = self._mutation_state(source, target)
            if state == "completed":
                return state
            if attempt + 1 < attempts:
                time.sleep(delay)
        return state

    def rename(
        self,
        source_path: str,
        target_path: str,
        *,
        accept_already_completed: bool = False,
    ) -> MutationReceipt:
        self._require_login()
        source = require_within(source_path, self.config.allowed_root)
        target = require_within(target_path, self.config.allowed_root)
        if source == target:
            return MutationReceipt("rename", source, target, "no_change")
        if posixpath.dirname(source) != posixpath.dirname(target):
            raise NasPathError("Rename 只允许同一目录；跨目录请使用 move")
        validate_component(posixpath.basename(source), label="改名源文件名")
        validate_component(posixpath.basename(target), label="改名目标文件名")
        preflight = self._preflight_mutation("rename", source, target)
        if preflight == "already_completed":
            if accept_already_completed:
                return MutationReceipt(
                    "rename", source, target, "preflight_reconciliation"
                )
            raise NasOutcomeUnknown(
                "rename", source, target, "源不存在且目标存在，但没有本次操作回执"
            )
        request_error: Optional[NasError] = None
        try:
            self._post_json(
                "/webapi/entry.cgi",
                {
                    "api": "SYNO.FileStation.Rename",
                    "version": "2",
                    "method": "rename",
                    "path": json.dumps([file_station_path(source)], ensure_ascii=False),
                    "name": json.dumps(
                        [posixpath.basename(target)], ensure_ascii=False
                    ),
                    "_sid": self.sid,
                },
            )
        except NasError as exc:
            request_error = exc
        state = self._reconcile_mutation("rename", source, target)
        if state == "completed":
            return MutationReceipt(
                "rename",
                source,
                target,
                "path_reconciliation"
                if request_error
                else "response+path_reconciliation",
            )
        if (
            request_error is not None
            and state == "pending"
            and not isinstance(request_error, NasTransportError)
        ):
            raise request_error
        raise NasOutcomeUnknown("rename", source, target, state)

    def move(
        self,
        source_path: str,
        destination_directory: str,
        *,
        create_directories: bool = False,
        accept_already_completed: bool = False,
        poll_timeout: Optional[float] = None,
        poll_interval: float = 0.25,
    ) -> MutationReceipt:
        self._require_login()
        source = require_within(source_path, self.config.allowed_root)
        destination = require_within(destination_directory, self.config.allowed_root)
        if create_directories:
            self.ensure_directory(destination)
        else:
            destination_entry = self.stat(destination)
            if destination_entry is None or not destination_entry.is_dir:
                raise NasNotFoundError("NAS 移动目标目录不存在")
        target = join_volume_path(destination, posixpath.basename(source))
        if source == target:
            return MutationReceipt("move", source, target, "no_change")
        preflight = self._preflight_mutation("move", source, target)
        if preflight == "already_completed":
            if accept_already_completed:
                return MutationReceipt(
                    "move", source, target, "preflight_reconciliation"
                )
            raise NasOutcomeUnknown(
                "move", source, target, "源不存在且目标存在，但没有本次操作回执"
            )

        request_error: Optional[NasError] = None
        task_started = False
        try:
            body = self._post_json(
                "/webapi/entry.cgi",
                {
                    "api": "SYNO.FileStation.CopyMove",
                    "version": "3",
                    "method": "start",
                    "path": json.dumps([file_station_path(source)], ensure_ascii=False),
                    "dest_folder_path": file_station_path(destination),
                    "overwrite": "false",
                    "remove_src": "true",
                    "accurate_progress": "true",
                    "_sid": self.sid,
                },
                timeout=(self.config.connect_timeout, self.config.transfer_timeout),
            )
            task_started = True
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            task_id = str(data.get("taskid") or "")
            if task_id:
                deadline = time.monotonic() + (
                    poll_timeout
                    if poll_timeout is not None
                    else self.config.transfer_timeout
                )
                while True:
                    if time.monotonic() >= deadline:
                        raise NasOutcomeUnknown(
                            "move", source, target, "异步任务状态超时"
                        )
                    status_body = self._post_json(
                        "/webapi/entry.cgi",
                        {
                            "api": "SYNO.FileStation.CopyMove",
                            "version": "3",
                            "method": "status",
                            "taskid": task_id,
                            "_sid": self.sid,
                        },
                    )
                    status_data = (
                        status_body.get("data")
                        if isinstance(status_body.get("data"), dict)
                        else {}
                    )
                    if status_data.get("finished") is True:
                        errors = status_data.get("errors") or []
                        if errors:
                            raise NasApiError(
                                "SYNO.FileStation.CopyMove",
                                "status",
                                f"task_errors:{len(errors)}",
                            )
                        break
                    time.sleep(poll_interval)
        except NasError as exc:
            request_error = exc

        state = self._reconcile_mutation("move", source, target)
        if state == "completed":
            return MutationReceipt(
                "move",
                source,
                target,
                "path_reconciliation"
                if request_error
                else "response+path_reconciliation",
            )
        if (
            request_error is not None
            and not task_started
            and not isinstance(request_error, NasTransportError)
        ):
            raise request_error
        raise NasOutcomeUnknown("move", source, target, state)


__all__ = [
    "DownloadReceipt",
    "DownloadStream",
    "MutationReceipt",
    "NasApiError",
    "NasAuthenticationError",
    "NasConfigurationError",
    "NasConflictError",
    "NasError",
    "NasNotFoundError",
    "NasOutcomeUnknown",
    "NasPathError",
    "NasPermissionError",
    "NasProtocolError",
    "NasTransportError",
    "RemoteEntry",
    "SynologyConfig",
    "SynologyFileStation",
    "UploadReceipt",
    "file_station_path",
    "join_volume_path",
    "normalize_volume_path",
    "require_within",
    "select_entries",
    "validate_component",
    "volume_path_from_file_station",
]
