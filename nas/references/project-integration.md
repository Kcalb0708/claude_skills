# 新项目接入指南

本指南用于把群晖 File Station 能力写入一个已有或新建项目。先完成项目读取和需求裁剪，再实现 provider；技能目录中的脚本是冒烟工具，不是生产依赖。

## 0. 先复用已有实现

先检查已有业务入口、组件、Port、adapter 和实际装配；满足需求时直接复用，缺少窄能力时在既有边界补齐。仅当目标项目没有 NAS 实现，或用户明确要求替换且已完成契约差异分析时，才以 [synology_file_station.py](../assets/python/synology_file_station.py) 为起点迁入并适配。原子载荷与响应提取查 [file-station-api.md](file-station-api.md)，完整功能的多接口组合查 [implementation-flows.md](implementation-flows.md)。不要先写另一套客户端再拿资产补漏洞。

需要 FastAPI Range 直通流式预览、且项目没有等价实现时，可迁入 [fastapi_nas_preview.py](../assets/python/fastapi_nas_preview.py)。桥接层通过 `acquire_client/release_client` 回调兼容每请求 client 或有界会话池；`discard=True` 表示连接/迭代/取消异常，会话池必须销毁而不是复用该 client。

推荐落点示例：

```text
src/<project>/infrastructure/nas/synology_file_station.py
tests/infrastructure/nas/test_synology_file_station.py
```

复制不是运行时依赖：项目部署后即使 Skill 安装目录不存在也必须能启动。保留资产中的请求契约和失败语义，删除项目不需要的能力可以，但相关调用和测试必须一起裁剪。

下表仅用于选择迁入本 Skill 资产的分支；已有项目采用自己的路径、回执、覆盖和恢复合同。

| 项目需求 | 资产入口 | 采用该资产时需覆盖的验收 |
| --- | --- | --- |
| 配置 | `SynologyConfig.from_env` | 缺键 fail fast、密码 `repr` 脱敏、URL/根目录格式校验 |
| 列举 | `list_entries` | 固定排序、实际条数推进 offset、短页/total/重复页停止 |
| 元数据 | `stat` | `getinfo`、408 仅表示不存在、响应路径仍经过沙箱 |
| 下载 | `open_download` / `download_to_file` | 关闭 response、JSON 错误体拒绝、原子替换、大小/哈希/媒体签名 |
| FastAPI 预览 | `nas_preview_response` | ETag 命中不访问 NAS、Range 安全透传、正常/异常/未消费只释放一次 |
| 上传 | `upload` | 默认不覆盖、写后 stat；高完整性用 `verify_content=True` 回读 SHA-256 |
| 建目录 | `ensure_directory` | 根必须预先存在、逐级 `force_parent=false` 创建并读回 |
| 改名 | `rename` | 同目录、源/目标预检、错误和超时后四态对账 |
| 移动 | `move` | CopyMove 异步轮询、任务完成后仍做四态对账 |

`rename/move` 默认不会把“源不存在、目标存在”自动当成本次成功，因为目标可能早已存在。只有调用方持有同一持久化操作的幂等键/计划/回执时，才传 `accept_already_completed=True`。这条边界必须在项目 service/worker 层表达，不能由 HTTP adapter 猜测。

## 1. 建立接入清单

在修改代码前记录下列事实；能从仓库确认的不要反问用户：

| 项目事实 | 需要确认的内容 |
| --- | --- |
| 运行时 | Python 版本、框架、实际解释器、依赖管理方式 |
| 配置 | 现有 `.env`/settings/secret manager、变量命名、`.gitignore` |
| 生命周期 | CLI 单次运行、定时任务、常驻 Web 服务、worker 数 |
| NAS 操作 | 只读列举、下载、上传、建目录、改名、移动、Range 预览中的实际子集 |
| 路径范围 | 允许根目录；是否只允许一层或固定业务目录形状 |
| 权限 | 只读账号还是受限写账号；哪些操作已获本次授权 |
| 状态归属 | NAS 只存文件，还是 NAS 上的 manifest 也是事实源；本地状态是缓存还是主数据 |
| 失败语义 | 可以重试、必须对账、必须人工处理的边界 |

只有以下信息通常需要用户或部署环境提供：当前 DSM 入口、允许根路径、操作授权和凭据是否已经安全配置。密码不得通过聊天收集。

## 2. 推荐的代码边界

业务层依赖窄接口，例如：

```python
class NasStorage(Protocol):
    def list_entries(self, directory: str) -> list[RemoteEntry]: ...
    def stat(self, path: str) -> RemoteEntry | None: ...
    def open_download(self, path: str, byte_range: str = "") -> DownloadStream: ...
    def upload(self, local_path: Path, directory: str, name: str, *, overwrite: bool = False) -> UploadReceipt: ...
    def rename(self, source: str, target: str) -> MutationReceipt: ...
    def move(self, source: str, destination_directory: str) -> MutationReceipt: ...
```

只声明项目实际需要的方法。群晖实现集中负责：

- DSM 登录/注销与 `requests.Session` 生命周期；
- `/volume1` 业务路径和 File Station API 路径之间的转换；
- 允许根校验、分页、JSON 业务码、流关闭；
- 超时与不确定结果的分类，不负责决定业务是否继续；
- 返回稳定的项目内 DTO/异常，不把 DSM 原始响应扩散到业务层。

路由、调度器、审核服务和迁移脚本调用这个适配层，不应各自拼 API 参数。生产代码应放在目标项目中，并遵循目标项目的包结构和依赖注入方式；不要 `import` 技能目录。

## 3. 配置契约

新项目默认采用：

| 变量 | 含义 | 要求 |
| --- | --- | --- |
| `NAS_DSM_URL` | `https://host:5001` 或可信 QuickConnect HTTPS 入口 | 不含 `/webapi`，不以 `/volume1` 结尾 |
| `NAS_USERNAME` | File Station 用户名 | 只读和写入账号按部署角色分开 |
| `NAS_PASSWORD` | 密码或 DSM 支持的应用密码 | 只存在于 secret manager/环境/被忽略的 `.env` |
| `NAS_ALLOWED_ROOT` | 业务可访问根，如 `/volume1/share/project` | 每次远程操作都要校验 |
| `NAS_VERIFY_TLS` | 是否校验证书 | 默认 true；不要把 false 提交成通用默认 |
| `NAS_CONNECT_TIMEOUT` | 建连超时 | 独立于读取超时 |
| `NAS_READ_TIMEOUT` | 普通请求/流读取超时 | 上传、移动任务可单独放宽 |

如果项目已有 `SYNOLOGY_*` 或领域专用变量，复用原命名并集中映射到一个 settings 对象，不要在业务代码中到处做双前缀回退。配置加载应 fail fast，但错误只能显示缺失的键名和配置文件路径，不能显示值。

应用启动时验证格式即可；是否在启动时真实登录取决于可用性要求。后台服务若允许 NAS 暂时离线，不应因为一次启动探测失败让整个应用无法启动，但必须暴露可观测的降级状态。

## 4. 双路径模型

业务状态保留带卷名的规范路径，适配器内部才转换：

```python
import re

WINDOWS_DRIVE = re.compile(r"^/?[A-Za-z]:[\\/]")

def normalize_volume_path(raw: str) -> str:
    value = str(raw or "").strip()
    if not value or "\x00" in value or "://" in value or WINDOWS_DRIVE.match(value):
        raise PathScopeError("非法 NAS 路径")
    parts = [part for part in value.replace("\\", "/").split("/") if part]
    if not parts or parts[0] != "volume1" or any(part in {".", ".."} for part in parts):
        raise PathScopeError("NAS 路径必须位于 /volume1")
    return "/" + "/".join(parts)

def require_within(path: str, allowed_root: str) -> str:
    normalized = normalize_volume_path(path)
    root = normalize_volume_path(allowed_root).rstrip("/")
    if normalized != root and not normalized.startswith(root + "/"):
        raise PathScopeError("NAS 路径超出允许根目录")
    return normalized

def file_station_path(volume_path: str) -> str:
    normalized = normalize_volume_path(volume_path)
    return normalized[len("/volume1"):] or "/"
```

如果部署确实使用其他卷名，应把允许卷作为显式配置扩展，而不是悄悄接受任意首段。File Station 响应中的 `path` 常为共享目录形式；进入业务状态前按项目路径契约转换或与请求上下文组合，不能在同一字段混存两种形式。

目录/文件名来自用户或第三方 API 时，单独使用组件清洗函数。清洗可以把 Windows 禁用字符替换为 `_`，但路径校验不能“清洗后继续”：包含 `.`、`..`、URL 或越界的完整路径必须直接拒绝。

## 5. 客户端生命周期与错误模型

建议异常至少分为：

- `NasConfigurationError`：缺配置或格式错误；不重试。
- `NasPathError`：路径非法或越界；不重试。
- `NasAuthenticationError`：账号、登录权限或二次验证问题；换凭据/策略前不重试。
- `NasPermissionError`：目标目录无对应权限；不通过其他开关绕过。
- `NasApiError`：已收到 DSM 业务失败，保留 `api`、`method`、`code` 和安全摘要。
- `NasTransportError`：DNS、TLS、连接、HTTP 或读超时；只读请求可以有限重试。
- `NasOutcomeUnknown`：写请求可能已经生效但未取得可靠回执；必须先对账。

异常对象不得保存密码、`sid`、完整 query URL、原始 multipart 或未经清洗的 `requests` 异常文本。日志使用 operation/request ID 关联，记录 API 名称、耗时、结果分类和非秘密路径；是否记录完整路径应遵循项目隐私策略。

CLI/定时任务通常每轮登录一次并在 `finally` 注销。Web 预览可用有界会话池，但 response 的生命周期长于路由函数，必须由流迭代器和 background cleanup 共同保证“只释放一次”。写操作不要复用已发生传输异常的会话。

## 6. 按能力实现

- **list/stat**：固定排序、完整分页；列表缓存只能限定在一次业务操作内，写后主动失效。
- **download**：返回可关闭的流对象，或下载到同目录临时文件并在校验后原子替换。任何分支都关闭 response。
- **upload**：先校验本地文件和远程冲突；默认不覆盖。回执至少含远程路径、文件名、本地大小、MIME、SHA-256、验证方式和时间。
- **rename**：只允许同一父目录；跨目录需求必须走 move。检查目标不存在。
- **move**：DSM CopyMove 是异步任务；轮询完成后仍检查源消失且目标存在。
- **range-stream**：只透传允许的单区间 `bytes=`，接受 200/206，传递 `Content-Length`、`Content-Range`、`Accept-Ranges`、`Last-Modified`，并用稳定内容版本生成私有 ETag。

具体 wire contract 见 [file-station-api.md](file-station-api.md)。写操作、流式响应和持久化恢复策略见 [advanced-workflows.md](advanced-workflows.md)。

## 7. 最低测试矩阵

使用 fake session/response 或项目现有 HTTP mock；测试中不得放真实账号密码。

| 类别 | 必测情形 |
| --- | --- |
| 配置 | 环境优先、缺键 fail fast、日志/异常不含秘密 |
| 路径 | 中文、反斜杠、精确根、相邻前缀越界、`.`/`..`、Windows 盘符、Git Bash 改写、URL |
| 登录 | POST 参数、缺 sid、业务失败、finally 注销 |
| 列举 | 1000+ 条分页、空页、`total` 缺失/异常、稳定排序 |
| 下载 | 200、206、JSON 错误体、半途断流、临时文件清理、签名失败不替换旧目标 |
| 上传 | multipart 字段、sid 位置、默认不覆盖、目标已存在、响应中断后目标回读 |
| 改名/移动 | 成功、冲突、源缺失、API 报错但目标已生效、源和目标异常组合、任务超时 |
| Web 流 | Range、304、客户端未消费 body、迭代中异常、会话仅释放一次 |
| 并发 | 同一业务键去重、队列容量、单 writer、重启恢复 |
| 迁移 | 全量预检失败零写入、已完成项幂等、逐项日志、部分失败可恢复 |

测试通过只证明本地契约，不证明端点、证书、账号或目录权限当前可用。

## 8. 真实验证顺序

1. 确认 `.gitignore` 已忽略凭据文件，只检查键名是否存在，不打印值。
2. 用 `probe_synology_access.py` 登录并非递归列出 `NAS_ALLOWED_ROOT`。
3. 若需要下载，选一个用户指定或仓库配置中已知的小文件，校验大小、MIME、签名和 SHA-256。
4. 若需要上传，先本地 dry-run；再由用户确认专用测试目录和覆盖策略，上传唯一文件名并 `getinfo/list` 回读。内容一致性要求高时再下载计算 SHA-256，或使用已确认可用的 DSM MD5 能力。
5. 改名/移动测试必须使用专用测试文件，执行前保存计划，执行后核对源与目标。删除清理属于独立破坏性操作，只有用户明确授权才执行。

完成标准不是“请求没有抛异常”，而是代码测试通过、真实所需能力逐项得到安全回执，并明确记录尚未验证的边界。

## 9. 已发生过的返工点及固定解法

这张表是接入审查清单。命中左列时直接采用右列，不再现场试错。

| 返工症状 | 根因 | 固定解法 |
| --- | --- | --- |
| `/volume1/...` 被当成 HTTP 地址或直接传给 File Station | DSM 入口与业务路径混成一个概念 | 业务保存卷路径；仅在 adapter 边界用 `file_station_path` 去掉 `/volume1` |
| 中文路径在 Git Bash/Windows 下变成盘符路径 | MSYS 自动路径转换或使用了本地 Path 语义 | 远程路径坚持 POSIX 字符串；Git Bash 命令设置 `MSYS_NO_PATHCONV=1`；路径校验拒绝盘符 |
| HTTP 200 被记录为成功但 DSM 实际失败 | 未检查 JSON `success` 和 `error.code` | 所有 JSON 调用统一经过 `_post_json`，四层检查后才返回 |
| 目录超过一页后漏文件或死循环 | 只取第一页、按 page size 而非实际条数推进、服务端重复页 | 固定 name 升序；offset 加实际返回数；空页/短页/total/重复页均有结束或报错 |
| 上传传 `overwrite=false` 却静默跳过 | 不同 DSM 版本对 multipart 布尔参数处理不一致 | 默认不覆盖时先 `stat`，并省略 `overwrite` 字段；成功后再次 `stat` |
| 请求超时后直接重试，产生重复文件或错误移动 | 把“没收到响应”误判为“没有执行” | 上传回读目标；rename/move 按源/目标四态对账；证据不唯一抛 `NasOutcomeUnknown` |
| 迁移重跑把旧目标误认成本次成功 | adapter 不知道是否存在同一持久化意图 | 默认拒绝 target-only；仅凭同一 operation 记录使用 `accept_already_completed=True` |
| 视频预览一段时间后连接池耗尽 | response 未消费、客户端取消或异常时没有归还/销毁会话 | 下载流显式拥有 response；生成器 `finally` 与 background cleanup 共用一次性释放器 |
| 单元测试全绿后仍无法部署 | 测试解释器与服务解释器不同，或只测 adapter 未走业务入口 | 用生产实际解释器安装/import/test/start，再从 HTTP/任务/worker 入口跑最小样本 |
| dry-run 被当成写权限验证 | 只验证了本地计划，没有产生 NAS 副作用 | 写入已接通必须有已授权测试目录、唯一文件名、写入回执及 stat/哈希回读 |
| 多 worker 偶发重复写 | 把 `RLock` 当成跨进程锁 | 单独 mutation worker，或数据库/Redis 原子 claim、唯一幂等约束和持久操作日志 |
