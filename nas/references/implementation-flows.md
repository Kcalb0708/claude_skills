# NAS 功能实现流转

本文件描述“一个业务功能如何组合多个原子接口”。原子请求、载荷位置、响应字段和过滤方法见 [file-station-api.md](file-station-api.md)；写入恢复、持久队列和多 worker 约束见 [advanced-workflows.md](advanced-workflows.md)。实现新功能时先在下表选流程，再复用代码资产，不要把某个 DSM API 的 HTTP 200 当成整个功能完成。

## 使用边界

下表和示例是本 Skill 资产的调用链，不是所有已有项目都必须迁移到的实现。先核对项目实际 API、Port、响应和回执。上传自动创建父目录与独立建空目录是不同能力；常驻会话与脚本会话应分别管理；成功后回读和覆盖规则按项目明确的业务合同选择，不混称为所有实现都具备。

## 功能到调用链

| 业务功能 | 必需调用链 | 最终可交付结果 |
| --- | --- | --- |
| 浏览并筛选素材 | login → List 全分页 → 路径标准化/沙箱 → `select_entries` → logout | 稳定排序的 `RemoteEntry` 子集 |
| 判断文件是否存在 | login → getinfo → 路径标准化/沙箱 → logout | `RemoteEntry` 或 `None` |
| 校验后下载 | login → getinfo（取得预期大小）→ Download 流 → `.part` → 大小/签名/SHA-256 → 原子替换 → logout | `DownloadReceipt` 和已验证本地文件 |
| 安全创建目录 | login → getinfo 允许根 → 逐级 getinfo → 缺层 CreateFolder → 每层 getinfo 回读 → logout | 已确认存在的规范目录路径 |
| 安全上传 | 本地预检 → login → getinfo 目标目录 → getinfo 目标文件 → 可选逐级建目录 → Upload → getinfo 回读 → 可选 Download SHA-256 回读 → logout | `UploadReceipt` 或明确的 `NasOutcomeUnknown` |
| 同目录改名 | 持久化意图 → login → source/target getinfo → Rename → source/target getinfo 对账 → 持久化回执 → logout | `MutationReceipt` 或 `reconciliation_required` |
| 跨目录移动 | 持久化意图 → login → 目标目录 getinfo/创建 → source/target getinfo → CopyMove start → CopyMove status → source/target getinfo 对账 → 持久化回执 → logout | `MutationReceipt` 或 `reconciliation_required` |
| FastAPI Range 预览 | 业务记录取 path/version → ETag 判断 → acquire client → Download + Range → StreamingResponse → close response → release/discard client | 304、200 或 206 响应，且无会话泄漏 |
| 批量上传/迁移 | 保存计划 → 全量 List/getinfo 预检 → 冲突为零 → 单 writer 逐项执行 → 每项对账并保存回执 → 重建 projection | 可恢复批次及逐项状态，失败不丢进度 |
| NAS 素材交给外部平台 | NAS getinfo/download → 本地签名/大小/hash → 外部平台上传 → 校验平台业务码 → 保存两段回执 | NAS 文件回执 + 平台素材 ID/回执 |

## 共享会话骨架

以下是一次性脚本的 session 示例。常驻服务按项目生命周期复用已登录 client，单次请求不应注销共享会话；shutdown 和不可恢复错误按实际资源所有权关闭。一次性脚本保证异常退出关闭：

```python
from synology_file_station import SynologyConfig, SynologyFileStation


def run_nas_operation():
    config = SynologyConfig.from_env()
    with SynologyFileStation(config) as client:
        return execute_business_flow(client, config)
```

CLI 和定时任务通常每轮创建一次。Web 预览可以从有界池借用，但必须通过 `release_client(client, discard)` 区分可复用和应销毁的会话。多步功能中不要每调一个 DSM API 就重新登录。

## 流程一：浏览、提取与过滤

调用顺序：

```text
login
  → List(offset=0, sort=name asc)
  → List(offset += 实际返回数)，直到空页/短页/达到 total
  → 每条 path 恢复成 /volume1/... 并重新检查 NAS_ALLOWED_ROOT
  → RemoteEntry
  → 按文件/目录、扩展名、名称、大小过滤
logout
```

直接复用：

```python
images = client.list_files(
    "/volume1/share/project/inbox",
    suffixes=("jpg", "jpeg", "png", "webp"),
    name_contains="待审",
    min_size=1,
    max_size=20 * 1024 * 1024,
)
image_paths = [entry.path for entry in images]
```

`list_files` 内部已经执行完整分页，再使用 `select_entries` 过滤。不要在只取第一页的 raw `data.files` 上过滤，否则目录变大后会漏数据。业务层只消费 `RemoteEntry`；需要额外字段时先扩展统一 DTO 和解析器。

完成判据：返回条目全部在允许根内、顺序稳定、过滤条件有确定语义，并能区分“空目录”和“列表请求失败”。

## 流程二：校验后下载

调用顺序：

```text
stat(remote_path)
  → 不存在则停止
  → 确认不是目录并取得 expected_size
open_download(remote_path)
  → 拒绝非 200/206 和 JSON 错误体
  → 写同目录随机 .part
  → 校验 Content-Length / expected_size / 非空
  → 计算 SHA-256
  → 媒体场景校验扩展名与文件签名
  → os.replace 原子替换目标
  → DownloadReceipt
```

直接复用：

```python
from pathlib import Path

remote = client.stat(remote_path)
if remote is None or remote.is_dir:
    raise ValueError("NAS 素材不存在或不是文件")

receipt = client.download_to_file(
    remote.path,
    Path("staging/material.jpg"),
    expected_size=remote.size,
    verify_media=True,
)
```

下载中断、大小不符、签名不符或哈希不符时，旧目标文件保持不变，临时文件被清理。若业务已经保存预期 SHA-256，同时传 `expected_sha256`；这能发现 NAS 内容被同名替换。

完成判据：调用方得到的是经过验证的本地文件和 `DownloadReceipt`，不是 NAS URL，也不是未经识别的响应字节。

## 流程三：安全创建目录

调用顺序：

```text
stat(NAS_ALLOWED_ROOT)
  → 根不存在或不是目录则停止
对目标相对根的每个组件：
  → stat(current/component)
  → 已存在且是目录：进入下一层
  → 已存在但不是目录：冲突并停止
  → 不存在：CreateFolder(parent=current, name=component, force_parent=false)
  → stat 回读确认目录
```

直接复用：

```python
directory = client.ensure_directory(
    "/volume1/share/project/2026/09/素材"
)
```

`NAS_ALLOWED_ROOT` 自身必须由管理员或部署流程预先创建，通用业务代码不能越过允许根自动建共享目录。逐层回读可以防止负责人、项目名或日期拼错后由 `force_parent=true` 一次制造整棵错误目录。

完成判据：每个新层级都有 CreateFolder 回执和 getinfo 回读，返回路径仍在允许根内。

## 流程四：安全上传与回读

调用顺序：

```text
本地文件存在、非空、类型/签名/大小符合业务规则
  → 计算本地 SHA-256
  → stat(destination_directory)
  → 可选 ensure_directory
  → stat(target_path)
  → 已存在且 overwrite=false：停止
  → Upload multipart（默认省略 overwrite）
  → stat(target_path) 回读名称、类型和大小
  → 高完整性场景 remote_sha256(target_path)
  → 与本地 size/hash 比较
  → UploadReceipt
```

直接复用：

```python
receipt = client.upload(
    local_path,
    "/volume1/share/project/inbox",
    remote_name="material-20260912.jpg",
    create_directories=True,
    overwrite=False,
    verify_content=True,
)
```

异常流转：

```text
Upload 返回明确业务失败
  → stat(target)
  → 目标没有出现：保留原 API 错误
  → 新目标出现且大小匹配：记录 directory_reconciliation

Upload 超时/断连/非确定响应
  → stat(target)
  → 新目标出现且大小匹配：可记录目录对账；严格场景继续 SHA-256 回读
  → 目标不存在、覆盖前已存在或大小不符：NasOutcomeUnknown
  → 持久层写 reconciliation_required，禁止直接重复上传
```

批量上传必须先对所有本地文件、远程目标、重复文件名和路径范围做完整预检，再执行第一项 Upload。部分成功时保存逐项回执，不能只返回批次失败。

完成判据：至少取得目标 `stat` 回读；内容正确性属于需求时必须取得 SHA-256 回读，且失败状态能区分确定失败和结果未知。

## 流程五：改名

调用顺序：

```text
先保存 operation_id、source、target、动作和幂等键
  → stat(source) + stat(target)
  → source 有 / target 无：允许 Rename
  → source 无 / target 有：只有同一持久化操作重跑时可视为已完成
  → 两者都有：冲突，reconciliation_required
  → 两者都无：丢失，reconciliation_required
Rename
  → 再次 stat(source) + stat(target)，必要时短暂重查
  → source 无 / target 有：MutationReceipt
  → 其他组合：NasOutcomeUnknown
  → 保存最终状态和回执
```

直接复用：

```python
receipt = client.rename(source_path, target_path)

# 仅在确定这是同一个已持久化 operation 的恢复执行时：
receipt = client.rename(
    source_path,
    target_path,
    accept_already_completed=True,
)
```

Rename 只接受同一父目录。跨目录需求进入移动流程，不能把目标目录编码进 `name`。

完成判据：DSM 成功 JSON 之后仍完成源/目标对账，并把 `MutationReceipt` 与业务对象的稳定键关联保存。

## 流程六：跨目录移动

调用顺序：

```text
保存持久化操作意图
  → stat/ensure_directory(destination)
  → stat(source) + stat(destination/basename(source))
  → CopyMove.start(remove_src=true, overwrite=false)
  → 提取 data.taskid
  → CopyMove.status(taskid)，直到 finished=true
  → errors 必须为空
  → stat(source) + stat(target) 最终对账
  → 保存 MutationReceipt
```

直接复用：

```python
receipt = client.move(
    source_path,
    "/volume1/share/project/archive",
    create_directories=True,
)
```

`finished=true` 只说明 DSM 任务结束，不证明业务目标状态正确。start 响应中断、status 超时或 errors 非空后仍先对账；任务可能在客户端停止轮询后继续运行，因此不能立即提交第二个相同 CopyMove。

完成判据：源消失且目标出现；否则持久层进入 `reconciliation_required`，由后续对账任务或人工处理。

## 流程七：FastAPI Range 预览

调用顺序：

```text
从业务记录读取 remote_path、MIME、内容 hash 或不可变 version
  → 路径经过 NAS_ALLOWED_ROOT
  → 根据 hash/version 生成 ETag
  → If-None-Match 命中：直接 304，不借 NAS session
  → acquire_client()
  → open_download(remote_path, request Range)
  → 校验 200/206、Content-Type 非 JSON
  → 构造 StreamingResponse，透传安全响应头
  → 正常完整消费：close response，release_client(discard=false)
  → 未消费/取消/迭代异常：close response，release_client(discard=true)
  → BackgroundTask 与生成器 finally 只释放一次
```

直接复用：

```python
from fastapi_nas_preview import nas_preview_response, private_etag

return nas_preview_response(
    request,
    material.remote_path,
    acquire_client=nas_pool.acquire,
    release_client=nas_pool.release,
    media_type=material.mime_type,
    etag=private_etag(material.sha256 or material.version),
)
```

池的 `release(client, discard)` 必须在 `discard=True` 时注销并销毁会话；只有完整正常消费才允许回池。池还需要最大连接数、等待超时、空闲过期和应用 shutdown 清理。

完成判据：304 不访问 NAS；Range 需求实际得到 206 和正确 `Content-Range`；正常、未消费、部分消费取消和迭代异常四条路径都不泄漏 response/session。

## 流程八：批量迁移或审核流转

可靠批处理分为计划和执行两阶段：

```text
List/getinfo 收集事实
  → 生成完整 plan：operation_id、source、target、状态、幂等键
  → 全量检查越界、源、目标、批内重复目标
  → 任一 invalid：零写入
  → 把 plan 持久化为 prepared/queued
  → 单 writer 按稳定顺序执行 rename/move/upload
  → 每一项完成后立即保存回执或 reconciliation_required
  → 全部完成后更新 manifest/projection
  → 重跑时依据同一个 operation_id 对账，不重新生成副作用
```

多 worker/多实例不能只依赖进程内锁。使用数据库或 Redis 原子 claim、唯一幂等约束、重启恢复和退避策略。详细状态与反向补偿见 [advanced-workflows.md](advanced-workflows.md)。

完成判据：预检失败确实零写入；进程在任意一项后退出都能从持久记录恢复；结果未知项不会自动重放。

## 流程九：NAS 文件交给下游平台

NAS 和广告/内容平台是两段独立调用：

```text
NAS stat/download
  → 本地文件签名、大小、MIME、SHA-256
  → 下游平台 multipart/upload API
  → 同时检查下游 HTTP 与业务 code
  → 提取平台 material_id/file_id/url 等业务字段
  → 保存 NAS remote_path/hash + 平台 request_id/业务码/素材 ID
```

下游平台不能接收 `/volume1/...` 当作公网 URL，也不能把 NAS 路径当成平台素材 ID。若下游上传失败，NAS 下载回执仍可保留用于安全重试；若下游响应中断，按下游平台的查询或幂等能力对账，不能拿 NAS 的真值表代替。

完成判据：两段回执分别可追踪，失败边界清楚，最终业务记录关联的是经过验证的内容 hash，而不是偶然的本地临时路径。
