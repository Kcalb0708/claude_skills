"""FastAPI 可选桥接：把 NAS 下载流安全地交给 StreamingResponse。

复制到使用 FastAPI 预览的目标项目；普通 CLI/worker 项目不需要本文件。
"""

from __future__ import annotations

import hashlib
import threading
from contextlib import suppress
from typing import Callable, Iterator, Optional

from fastapi import Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from synology_file_station import DownloadStream, SynologyFileStation

AcquireClient = Callable[[], SynologyFileStation]
ReleaseClient = Callable[[SynologyFileStation, bool], None]
_PASSTHROUGH_HEADERS = (
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "Last-Modified",
)


def private_etag(version: str) -> str:
    """从业务侧稳定版本或内容哈希生成不泄露路径的强 ETag。"""

    value = str(version or "")
    if not value:
        raise ValueError("ETag version 不能为空")
    return '"' + hashlib.sha256(value.encode("utf-8")).hexdigest() + '"'


def _normalize_etag(etag: str) -> str:
    value = str(etag or "").strip()
    if not value:
        return ""
    if "\r" in value or "\n" in value:
        raise ValueError("ETag 不能包含换行")
    if value.startswith("W/"):
        value = value[2:].strip()
    if not (value.startswith('"') and value.endswith('"')):
        value = f'"{value}"'
    return value


def request_etag_matches(request: Request, etag: str) -> bool:
    expected = _normalize_etag(etag)
    if not expected:
        return False
    raw = str(request.headers.get("if-none-match") or "")
    for candidate in raw.split(","):
        value = candidate.strip()
        if value == "*" or _normalize_etag(value) == expected:
            return True
    return False


def _discard_safely(
    release_client: ReleaseClient, client: SynologyFileStation, discard: bool
) -> None:
    with suppress(Exception):
        release_client(client, discard)
    # 清理回调不能覆盖原始 HTTP/NAS 错误；项目应在回调内部记录安全日志。


class _StreamLease:
    """让生成器 finally 与 BackgroundTask 竞争时最多释放一次。"""

    def __init__(
        self,
        client: SynologyFileStation,
        stream: DownloadStream,
        release_client: ReleaseClient,
    ) -> None:
        self.client = client
        self.stream = stream
        self.release_client = release_client
        self._lock = threading.Lock()
        self._released = False

    def release(self, discard: bool) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        try:
            self.stream.close()
        finally:
            self.release_client(self.client, discard)


def nas_preview_response(
    request: Request,
    remote_path: str,
    *,
    acquire_client: AcquireClient,
    release_client: ReleaseClient,
    media_type: Optional[str] = None,
    etag: str = "",
    cache_control: str = "private, max-age=0",
    chunk_size: int = 1024 * 1024,
) -> Response:
    """构造 NAS 预览响应，覆盖未消费、取消、异常和正常完成四条释放路径。

    ``release_client(client, discard)`` 在正常完整消费时收到 ``False``；连接、
    迭代或取消异常以及从未消费 body 时收到 ``True``，会话池应销毁该 client。
    """

    normalized_etag = _normalize_etag(etag)
    if normalized_etag and request_etag_matches(request, normalized_etag):
        return Response(
            status_code=304,
            headers={
                "ETag": normalized_etag,
                "Cache-Control": cache_control,
                "Vary": "Range",
            },
        )

    client = acquire_client()
    try:
        stream = client.open_download(
            remote_path, str(request.headers.get("range") or "")
        )
    except Exception:
        _discard_safely(release_client, client, True)
        raise

    lease = _StreamLease(client, stream, release_client)
    headers = {
        name: str(stream.headers[name])
        for name in _PASSTHROUGH_HEADERS
        if stream.headers.get(name) not in {None, ""}
    }
    headers.setdefault("Accept-Ranges", "bytes")
    headers["Cache-Control"] = cache_control
    headers["Vary"] = "Range"
    if normalized_etag:
        headers["ETag"] = normalized_etag
    resolved_media_type = (
        media_type
        or str(stream.headers.get("Content-Type") or "application/octet-stream").split(
            ";", 1
        )[0]
    )

    def body() -> Iterator[bytes]:
        discard = True
        try:
            for chunk in stream.iter_bytes(chunk_size=chunk_size):
                yield chunk
            discard = False
        finally:
            lease.release(discard)

    try:
        return StreamingResponse(
            body(),
            status_code=stream.status_code,
            media_type=resolved_media_type,
            headers=headers,
            background=BackgroundTask(lease.release, True),
        )
    except Exception:
        with suppress(Exception):
            lease.release(True)
        raise


__all__ = ["nas_preview_response", "private_etag", "request_etag_matches"]
