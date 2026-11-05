# File Station 能力、载荷与响应目录

本目录回答四件事：业务功能调用哪个代码入口、底层使用哪个 DSM 接口、载荷放在哪里、响应中提取什么。本页表格描述 Skill 自带资产，不代表目标项目已开放这些方法或 API。已有项目先复用其 adapter；缺少实现的 Python/FastAPI 项目可参考 [synology_file_station.py](../assets/python/synology_file_station.py)，需要 HTTP 流式预览时再参考 [fastapi_nas_preview.py](../assets/python/fastapi_nas_preview.py)。不要在业务路由中照表重新拼 HTTP。

不同 DSM 版本或反向代理可能改变可用 API 版本；遇到版本错误时先查询 `SYNO.API.Info` 或查对应 DSM 官方文档，不要通过反复修改业务参数猜测。

项目路由、权限和已有调用链必须从目标代码核实，记录在目标项目文档中；本页仅维护通用 DSM 接口与随包资产合同。

## 快速能力选择

| 需要的功能 | 直接调用的资产入口 | DSM API / method | 载荷位置 | 稳定返回或提取结果 |
| --- | --- | --- | --- | --- |
| 建立会话 | `client.login()` 或 `with SynologyFileStation(config)` | `SYNO.API.Auth/login` v6 | POST form | 提取 `data.sid`，只保存在 client 内 |
| 关闭会话 | `client.close()` / 上下文退出 | `SYNO.API.Auth/logout` v6 | POST form | 不向业务层返回 DSM 原文；本地连接始终关闭 |
| 列目录 | `client.list_entries(path)` | `SYNO.FileStation.List/list` v2 | POST form | `Sequence[RemoteEntry]`，自动合并所有分页 |
| 只取文件并过滤 | `client.list_files(path, ...)` | 同上 | 同上 | 按扩展名、名称、最小/最大大小过滤后的 `RemoteEntry` |
| 对已有条目再过滤 | `select_entries(entries, ...)` | 不请求 NAS | 本地函数参数 | 可按 `all/file/directory`、扩展名、名称、大小组合过滤 |
| 查文件或目录 | `client.stat(path)` | `SYNO.FileStation.List/getinfo` v2 | POST form | `RemoteEntry`；DSM code 408 返回 `None` |
| 逐级建目录 | `client.ensure_directory(path)` | `SYNO.FileStation.CreateFolder/create` v2 | POST form | 返回规范 `/volume1/...` 目录路径 |
| 打开字节流/Range | `client.open_download(path, range_header)` | `SYNO.FileStation.Download/download` v2 | GET query + 可选 Range header | `DownloadStream`，包含状态、响应头和字节迭代器 |
| 原子下载到本地 | `client.download_to_file(...)` | 同上 | 同上 | `DownloadReceipt`：路径、大小、MIME、SHA-256、验证方式 |
| 计算远程哈希 | `client.remote_sha256(path)` | 同上 | GET query | `(size, sha256)`，不把完整文件留在本地 |
| 上传 | `client.upload(...)` | `SYNO.FileStation.Upload/upload` v2 | POST multipart；`sid` 在 query | `UploadReceipt`；默认 stat 回读，可选 SHA-256 回读 |
| 同目录改名 | `client.rename(source, target)` | `SYNO.FileStation.Rename/rename` v2 | POST form | `MutationReceipt`，源/目标对账后才成功 |
| 跨目录移动 | `client.move(source, destination)` | `SYNO.FileStation.CopyMove/start/status` v3 | POST form | `MutationReceipt`，异步任务完成并对账后才成功 |
| FastAPI 预览 | `nas_preview_response(...)` | 内部调用 Download | HTTP Request + client acquire/release 回调 | `304 Response` 或带 Range/ETag 的 `StreamingResponse` |

## 最短复用方式

```python
from synology_file_station import SynologyConfig, SynologyFileStation

config = SynologyConfig.from_env()
with SynologyFileStation(config) as client:
    images = client.list_files(
        config.allowed_root,
        suffixes=(".jpg", ".jpeg", ".png", ".webp"),
        name_contains="待审",
        min_size=1,
    )
    first = images[0] if images else None
```

`RemoteEntry` 只暴露稳定字段：

```text
path         规范 /volume1/... 路径
name         单个文件或目录名
is_dir       是否目录
size         字节数；DSM 未返回时为 None
modified_at  Unix mtime；DSM 未返回时为 None
```

常用提取与过滤：

```python
from synology_file_station import select_entries

all_entries = client.list_entries(config.allowed_root)
directories = select_entries(all_entries, kind="directory")
large_videos = select_entries(
    all_entries,
    kind="file",
    suffixes=("mp4", "mov"),        # 带不带点都可以
    name_contains="2026",
    min_size=10 * 1024 * 1024,
)
paths = [entry.path for entry in large_videos]
```

过滤只作用于已经标准化的 `RemoteEntry`，不会把 DSM 原始对象扩散到业务层。业务若需要 ACL、owner 或额外字段，应先扩展 `RemoteEntry` 和统一解析，再使用；不要让调用方直接读取 raw JSON。

## 通用规则

- DSM 入口是 `https://<host>:5001`、受信任反向代理或 QuickConnect HTTPS 入口；请求路径再拼 `/webapi/auth.cgi` 或 `/webapi/entry.cgi`。
- 登录和普通 JSON 操作优先 POST 表单。下载需要 GET 流；multipart 上传的 `sid` 放 query 时，错误日志必须去掉完整 URL。
- File Station 请求边界按 adapter 的规范化方法使用 `/<共享目录>/...`；本 Skill 资产的业务输入为 `/volume1/...`，目标项目的业务输入也可能是部署共享根相对路径。JSON 数组参数用 `json.dumps([...], ensure_ascii=False)`，不要手写带中文的 JSON 字符串。
- 每个 JSON 响应要求 HTTP 成功、对象类型、`success is True`。失败时安全提取 `error.code`；未知码原样保留为数字，不输出请求秘密。
- 连接和读取使用显式 timeout。只读操作可有限重试；写请求超时先对账，不能直接重试。

## 认证

### 登录

```text
POST /webapi/auth.cgi
api=SYNO.API.Auth
version=6
method=login
account=<username>
passwd=<password>
session=FileStation
format=sid
```

典型成功响应：

```json
{"success": true, "data": {"sid": "<secret>"}}
```

只提取非空 `data.sid` 并保存在 client 内；业务 DTO、日志和最终答复都不返回它。密码只在 POST form 中出现；`sid` 随后也按秘密处理。

### 注销

```text
POST /webapi/auth.cgi
api=SYNO.API.Auth
version=6
method=logout
session=FileStation
_sid=<sid>
```

注销失败通常不掩盖原业务结果，但本地 session/连接仍要关闭并清空 `sid`。

已验证的认证错误提示映射：

| code | 解释 |
| --- | --- |
| 400 | 账号不存在或密码错误 |
| 401 | 账号停用 |
| 402 | 没有登录 File Station 的权限 |
| 403 | 需要二次验证 |
| 404 | 二次验证码失败 |

业务码具有 API 上下文，同一个数字不要跨 API 复用同一文案。

## 列目录

```text
POST /webapi/entry.cgi
api=SYNO.FileStation.List
version=2
method=list
folder_path=/<share>/<directory>
additional=["real_path","size","time","type"]
offset=0
limit=500 或 1000
sort_by=name
sort_direction=asc
filetype=all              # 需要时
_sid=<sid>
```

典型成功响应：

```json
{
  "success": true,
  "data": {
    "files": [
      {
        "path": "/share/project/a.jpg",
        "name": "a.jpg",
        "isdir": false,
        "additional": {"size": 1234, "time": {"mtime": 1750000000}}
      }
    ],
    "total": 1
  }
}
```

`additional` 是 JSON 数组字符串。适配器只提取 `path/name/isdir/size/mtime`，将路径恢复为 `/volume1/...` 并再次检查允许根。累加实际返回条数作为下页 offset；出现空页、短页或 `offset >= total` 即结束。若服务端返回重复页面，应停止并报协议错误，避免无限循环。

## 读取条目信息

```text
GET 或 POST /webapi/entry.cgi
api=SYNO.FileStation.List
version=2
method=getinfo
path=["/<share>/<path>"]
additional=["real_path","size","type","perm"]
_sid=<sid>
```

成功响应仍是 `{"success":true,"data":{"files":[...]}}`。适配器只取 `data.files[0]` 并转成 `RemoteEntry`；空数组或业务码 `408` 返回 `None`，其他失败抛出分类异常。大小同时兼容 `additional.size` 与顶层 `size`。

## 建目录

```text
POST /webapi/entry.cgi
api=SYNO.FileStation.CreateFolder
version=2
method=create
folder_path=["/<share>/<existing-parent>"]
name=["<one-component>"]
force_parent=false 或受控使用 true
_sid=<sid>
```

DSM 成功响应通常只有 `{"success":true,"data":{...}}`，不同版本的 `data` 形状不作为业务契约。适配器忽略其中不稳定字段，随后用 `stat` 确认目标存在且 `is_dir=true`。

先确认 parent 在允许根内且存在。通用项目逐层建立明确计划中的目录并固定使用 `force_parent=false`。任何“已存在”类错误都通过目标回读确认，不能吞掉所有异常。

## 上传

```text
POST /webapi/entry.cgi?_sid=<sid>
multipart/form-data

api=SYNO.FileStation.Upload
version=2
method=upload
path=/<share>/<destination-directory>
create_parents=false（或在路径计划已验证时 true）
overwrite=<仅在明确覆盖时传 true>
file=(filename, binary stream, mime type)
```

代码中的承载方式：普通字段放 `data`，文件放 `files={"file": (name, handle, mime)}`，`sid` 单独放 `params={"_sid": sid}`。让 HTTP 库生成 multipart boundary。

DSM 上传成功响应的 `data` 在不同版本中不稳定，因此资产不从原响应提取文件 ID。稳定业务结果来自写后回读：`UploadReceipt` 提取远程规范路径、本地绝对路径、大小、MIME、SHA-256 和 `verified_by`。`verify_content=True` 时再下载远程内容并比对 SHA-256。

默认不覆盖时先 `stat` 检查冲突，并省略 `overwrite`；不要假设字符串 `false` 在所有 DSM 版本都表示“冲突即失败”，已有环境观察到它可能表现为静默跳过。

已验证的上传业务码：

| code | 解释 |
| --- | --- |
| 1800 | Content-Length 缺失或与实际大小不一致 |
| 1801 | 等待上传数据超时 |
| 1802 | multipart 最后一段缺文件名 |
| 1803 | 上传连接被取消 |
| 1804 | FAT 文件系统无法容纳该文件 |
| 1805 | 目标文件已存在 |

## 下载与 Range

```text
GET /webapi/entry.cgi
api=SYNO.FileStation.Download
version=2
method=download
path=/<share>/<file>
mode=download
_sid=<sid>
Range=bytes=<start>-<end>  # 可选，仅允许单区间
```

成功响应不是 JSON，而是文件字节及以下可提取响应头：

```text
HTTP 200 或 206
Content-Type
Content-Length
Content-Range      # Range 成功时
Accept-Ranges
Last-Modified
```

使用 `stream=True`。`open_download` 暴露经过校验的状态、headers 和 `iter_bytes()`；`download_to_file` 进一步提取大小、MIME、SHA-256 和验证方式。无 Range 通常接受 200；带 Range 接受 200 或 206，但视频 seek 需求必须验证 206 和正确的 `Content-Range`。状态异常或 `Content-Type` 为 JSON 时安全解析 `error.code`，绝不能把 JSON 错误体保存成媒体文件。调用方必须关闭 response，包括未开始消费、客户端取消和迭代中异常。

## 同目录改名

```text
POST /webapi/entry.cgi
api=SYNO.FileStation.Rename
version=2
method=rename
path=["/<share>/<parent>/<old-name>"]
name=["<new-name>"]
_sid=<sid>
```

成功 JSON 本身不作为最终结果；资产随后读取源和目标，只在“源不存在、目标存在”时返回 `MutationReceipt(operation/source_path/target_path/verified_by)`。执行前要求同一父目录、源存在、目标不存在；错误或断连后也使用同一状态表对账。

## 跨目录移动

启动：

```text
POST /webapi/entry.cgi
api=SYNO.FileStation.CopyMove
version=3
method=start
path=["/<share>/<source-file>"]
dest_folder_path=/<share>/<destination-directory>
overwrite=false
remove_src=true
accurate_progress=true
_sid=<sid>
```

若返回 `data.taskid`，使用单调时钟和明确截止时间轮询：

```text
POST /webapi/entry.cgi
api=SYNO.FileStation.CopyMove
version=3
method=status
taskid=<taskid>
_sid=<sid>
```

启动响应只提取 `data.taskid`：

```json
{"success": true, "data": {"taskid": "<task-id>"}}
```

状态响应只提取 `data.finished` 和 `data.errors`：

```json
{"success": true, "data": {"finished": true, "errors": []}}
```

只有 `finished is true` 且 `errors` 为空才结束轮询；之后仍核对源消失、目标出现，并返回标准 `MutationReceipt`。轮询超时不代表移动未发生，应进入路径对账。

## 安全错误信息

允许进入日志的字段：内部 operation ID、API 名称、method、HTTP status、DSM code、异常类名、耗时和经过项目策略允许的远程路径。

不得进入日志的字段：密码、`sid`、Cookie、完整请求 URL/query、Authorization、form body、multipart 内容、未经清洗的异常对象。特别是 `requests` 异常字符串常包含完整 URL；应从异常类型和 `response.status_code` 重新构造安全消息。

常见文件操作提示：`401` 检查参数/目标类型/权限，`403` 检查操作权限，`408` 检查路径是否存在。实际分类始终带上 API 名称，未知 DSM 版本应对照官方文档重新确认。
