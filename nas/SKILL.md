---
name: nas
description: 群晖 DSM File Station 的工程经验与可复用生产资产。用于按需求定位代码入口、原子 API、data/params/files/header 载荷、稳定响应提取与过滤、完整多接口流转，以及在用户明确要求时完成 Python/FastAPI 项目的 NAS 接入、迁移、预览或审核功能。用户提到群晖、DSM、QuickConnect、File Station、/volume1、NAS 素材或相关代码诊断与评审时使用；普通本地文件操作不使用。
---

# 群晖 NAS 工程经验与复用资产

## 定位

本 skill 是群晖 DSM File Station 的工程知识库和生产资产包，用来让 AI 在不同项目中复用已经验证过的接口契约、功能流转、失败语义和代码实现，避免重新猜参数、重复踩坑。

调用本 skill 后，产物由用户当前任务决定，而不是固定为“完成项目接入”：

- 用户询问接口、载荷、响应、过滤或实现方案时，产物是可直接采用的能力说明和完整调用链；
- 用户要求评审或诊断时，产物是基于当前代码的实际调用链、问题和修正建议；
- 用户要求执行一次 NAS 操作时，产物是与授权范围相符的操作结果或回执；
- 用户明确要求接入、实现、重构或跑通项目时，产物才是目标项目中的代码、项目本地测试和相应验证证据。

加载本 skill 本身不代表获得了修改目标项目、连接真实 NAS 或执行远程写操作的授权。“0→1 一次跑通”只约束项目实施分支：在该分支中一次性复用正确资产、接完整接口链并覆盖已知返工点，不是每次调用的默认副作用或固定结论。

## 先选择任务分支

根据用户当前请求只进入一个主分支；需要时可组合，但不能把后续分支的动作自动扩大到当前任务。

| 用户意图 | 应做什么 | 需要读取 | 完成点 |
| --- | --- | --- | --- |
| 查询接口、设计功能、索要代码示例 | 给出代码入口、DSM API、载荷位置、响应提取/过滤、完整调用链和失败语义 | [file-station-api.md](references/file-station-api.md)；涉及完整业务功能时再读 [implementation-flows.md](references/implementation-flows.md) | 信息足以让调用方按契约实现；不修改项目、不连接 NAS |
| 评审或诊断现有代码 | 读取目标代码，追踪实际调用链，再与资产和参考契约逐项比较 | 上述两份参考；按需检查 [synology_file_station.py](assets/python/synology_file_station.py) 或 [fastapi_nas_preview.py](assets/python/fastapi_nas_preview.py) | 区分代码确认、风险、缺口与未验证边界；不默认修复或实连 |
| 执行一次列举、统计、下载或上传 | 选择已随包提供的诊断脚本或生产客户端，先确认路径范围、配置和操作授权 | 本节“资源目录”；复杂写入再读 [advanced-workflows.md](references/advanced-workflows.md) | 返回真实结果或准确失败层级；远程写入必须在明确授权内 |
| 新项目接入、已有项目重构或要求跑通 | 读取项目事实，复用生产资产，适配配置和生命周期，在目标项目生成测试并按授权范围验证 | 必须读 [project-integration.md](references/project-integration.md)，再按能力读取其他参考 | 目标项目独立运行，所需业务链闭合，完成层级有证据 |

如果用户没有要求修改项目代码，目标项目文件保持只读；一次性 NAS 操作按用户明确请求和授权执行。如果用户要求“接入、实现、修复、重构、跑通”，则按实施分支完成工作；真实 NAS 写操作仍以用户明确给出的动作、目标范围和覆盖策略为边界。

## 先核对目标项目的复用能力

已有项目先追踪真实入口：页面/组件 → API → 服务或用例 → inward Port → adapter → DSM。核对 composition root 是否装配，不能只看函数名、旧注释或 Skill 自带资产推断项目能力。

复用优先级为：目标项目已有业务 API 和组件、服务/Port、adapter；确有缺口时才参考本 Skill 资产补齐。不要仅因项目使用 Python/FastAPI 就复制另一套 NAS 客户端、配置或预览服务，不引入现有异步链不需要的同步传输依赖。

能力记录分别标注“资产具备”“项目代码实现”“业务 API/界面已开放”“实际运行已验证”，四者不可互相替代。上传顺带创建父目录不等于存在独立新建空目录 API；图片专用上传不等于任意文件上传；个人只读文件选择器不等于共享素材库。

目标项目的源码地图、业务路由、配置别名和验证记录保存在目标项目自己的文档中，不放进通用 Skill。只将跨项目成立的经验提炼进 references，并注明适用条件与证据层级。

## 能力选择顺序

每次处理 NAS 功能都按以下顺序收敛，不能只找到一个接口就开始拼请求：

1. 核对目标项目已有实现后，再查 [file-station-api.md](references/file-station-api.md)，确定业务功能对应的生产代码入口、DSM API/version/method、HTTP 方法、载荷放置位置、典型响应、稳定字段和本地过滤方式。
2. 再查 [implementation-flows.md](references/implementation-flows.md)，确定该功能从登录、预检、主体请求、轮询或流消费，到回读、对账、回执和注销的完整顺序。
3. 目标项目缺少 NAS 实现时，Python/FastAPI 可迁入 [synology_file_station.py](assets/python/synology_file_station.py)；只有确需直通流式 Range 且项目没有等价实现时再迁入 [fastapi_nas_preview.py](assets/python/fastapi_nas_preview.py)。已有实现优先薄适配，协议参考不等于替换授权。
4. 只有项目涉及写入恢复、批量迁移、审核状态、多 worker、会话池或长连接流时，读取 [advanced-workflows.md](references/advanced-workflows.md)。
5. 只有项目接入、实现或重构任务，读取 [project-integration.md](references/project-integration.md) 并进入 0→1 实施流程。

一个功能的说明或实现至少要回答以下问题：

- 业务层直接调用哪个资产方法或项目 provider 方法；
- 底层使用哪个 DSM API、version 和 method；
- 请求使用 GET 还是 POST，字段分别放在 `data`、`params`、`files` 或 header 的哪里；
- DSM 成功和失败响应的结构是什么，哪些字段稳定、哪些原始字段不应向业务层扩散；
- 返回结果如何标准化，以及能按文件/目录、扩展名、名称、大小等条件过滤什么；
- 完整功能还要组合哪些接口，先后顺序、最终完成点和资源释放点是什么；
- 超时、断连、冲突、部分成功或结果未知时，哪些可以重试，哪些必须先回读或进入对账。

不要把一个原子请求当成完整业务功能。例如：

- 本 Skill 上传资产的默认流程是“本地预检 → 目标目录和冲突 `stat` → `Upload` → 目标 `stat` 回读 → 按需远程 SHA-256 回读”；已有项目若有明确的成功回执和异常对账合同，沿项目合同，不擅自增加正常成功后的回读或更改覆盖策略；
- 移动是“持久化意图 → 源/目标预检 → `CopyMove.start` → `CopyMove.status` → 源/目标最终对账”，不是只拿到 `taskid`；
- 下载是“`stat` → 流式 Download → `.part` → 大小/签名/SHA-256 校验 → 原子替换”，不是只收到 HTTP 200；
- Range 预览在返回响应后仍要覆盖正常消费、未消费、客户端取消和迭代异常的 response/session 释放。

## 资源目录

### 生产代码资产

- [synology_file_station.py](assets/python/synology_file_station.py)：统一的 `SynologyConfig`、路径沙箱、登录/注销、分页列举和过滤、`stat`、建目录、Range 下载、原子下载、远程 SHA-256、上传、改名、移动、类型化异常以及结果未知对账。
- [fastapi_nas_preview.py](assets/python/fastapi_nas_preview.py)：FastAPI 的 ETag/304、Range 流式响应和 response/session 一次性释放桥接。

仅在目标项目缺少所需底层实现时，把选定资产复制到项目自己的包中再做薄适配；已有 provider/adapter 时优先复用它。目标项目生产运行时不得从 Skill 安装目录动态 `import`。非 Python 项目以参考文档和资产行为作为协议契约做等价移植，不能只翻译函数名。

### 接口与流程知识

- [file-station-api.md](references/file-station-api.md)：功能到代码入口、DSM 原子接口、请求载荷位置、典型响应、稳定提取和本地过滤。
- [implementation-flows.md](references/implementation-flows.md)：浏览、下载、建目录、上传、改名、移动、Range 预览、批量迁移、审核流转和下游平台上传的完整多接口链。
- [project-integration.md](references/project-integration.md)：仅供项目接入/重构分支使用，包含适配边界、配置、生命周期、项目本地测试矩阵、真实验证和历史返工清单。
- [advanced-workflows.md](references/advanced-workflows.md)：仅在外部写入、结果恢复、持久队列、多进程或流式服务场景使用。

### 可选诊断与操作脚本

下列工具仅在发行包包含 `scripts/` 时可用。它们不是测试用例，也不会因加载 Skill 自动执行。调用前检查实际文件是否存在；精简包未携带脚本时使用项目已有入口，缺少入口时按用户需求适配代码资产，不引用不存在的脚本。

- `scripts/probe_synology_access.py`：真实登录并只读列举指定目录；默认不显示条目名称。
- `scripts/count_synology_materials.py`：只读统计目录中的图片素材数量。
- `scripts/fetch_synology_material.py`：从 NAS 读取文件，写入本地 `.part`，校验后原子落盘。
- `scripts/nas_env.py`：供上述工具及上传工具使用的配置读取、允许根与路径校验模块，不是独立操作入口。
- `scripts/upload_synology_material.py`：`--dry-run` 只做本地预检；不带 `--dry-run` 会真实写入 NAS，只能在远程写入已明确授权时执行。

这些脚本是按需使用的诊断/操作工具，不是目标项目的生产依赖。上传必须明确传入 `--remote-path`，不内置组织共享目录或按姓名拼路径。脚本间有依赖：probe 使用 count 中的客户端，四个入口均使用 nas_env；保留时应保留整个 scripts 目录。本 skill 不携带测试脚本；项目实施时依据 [project-integration.md](references/project-integration.md) 的最低矩阵，在目标项目中生成并维护项目本地测试。

## 分发与打包

- **开发指导包**：保留 `SKILL.md`、`references/`、`assets/python/`；可保留 `agents/openai.yaml` 及无凭据的 `.env.example`。可以省略整个 `scripts/`，接口契约、失败语义和可迁入代码资产仍完整。
- **带操作工具的完整包**：在上述基础上保留整个 `scripts/`，支持现成的连接检查、图片统计、下载和上传入口。
- 不打包目标项目的源码地图、业务专属规则、真实 `.env`、凭据、日志、历史备份、测试输出或 `__pycache__`。本 Skill 的生产资产不依赖 scripts；测试在目标项目内建立并维护。
- 文件夹名并不决定用途：`scripts/` 不等于测试。上传包按上述能力取舍；缺失工具不得伪称可用。

## 项目实施分支：0→1 一次接通

仅当用户要求接入、实现、重构或跑通时执行本节。

### 1. 读取项目事实

确认实际语言和版本、依赖管理、生产解释器、启动和测试入口、settings/secret manager、`.gitignore`、日志/异常约定、CLI/定时任务/Web/worker 生命周期、worker 数、现有存储边界、业务入口、所需 NAS 能力子集、允许根和读写授权。

能从仓库或环境确认的事实直接确认；只向用户索取无法推断且会改变实现的非秘密信息。不得要求用户把密码粘贴到聊天中。

### 2. 先形成能力蓝图

对每项业务需求完成“资产入口 → 原子接口与载荷 → 响应提取/过滤 → 完整多接口流转 → 失败/对账语义”的映射，再修改代码。业务功能必须以最终回执为完成点，而不是以某次 API 返回为完成点。

### 3. 迁入并适配生产资产

复用项目现有 NAS 传输实现，允许其通过不同 scope wrapper 表达业务权限；不要把多个权限包装器误判为重复客户端并进行无关合并。路由、任务和服务依赖项目 inward Port，不直接拼 DSM 请求。按项目风格适配包路径、配置、DTO、依赖注入和异常边界，同时保留所采用能力的路径沙箱、请求契约、分页、流释放及已明确的覆盖和对账语义；迁入本 Skill 上传资产时保留其默认不覆盖与写后核验合同。

### 4. 接通配置和生命周期

优先映射项目现有配置。新项目可采用：

```text
NAS_DSM_URL
NAS_USERNAME
NAS_PASSWORD
NAS_ALLOWED_ROOT
NAS_VERIFY_TLS=true
NAS_CONNECT_TIMEOUT=15
NAS_READ_TIMEOUT=60
NAS_TRANSFER_TIMEOUT=180
NAS_LIST_PAGE_SIZE=500
```

非秘密示例可进入 `.env.example`；真实凭据只来自 secret manager、进程环境或被忽略的本地配置。用部署时的同一解释器安装和 import-check 依赖。CLI/任务保证 `finally` 关闭；Web 流保证未消费、取消、异常、正常完成和 shutdown 都只释放一次。

### 5. 在目标项目生成离线测试

根据项目实际采用的能力，从接入指南的最低测试矩阵生成目标项目自己的 fake session/HTTP mock 测试。未采用的能力不必复制无关测试；采用上传、改名、移动、Range、多 worker 或迁移时，其关键失败和恢复语义不能省略。

离线测试禁止连接真实 NAS。测试通过只证明代码契约，不能据此报告端点、证书、账号或 ACL 已接通。

### 6. 按用户要求和授权验证

- 用户只要求实现代码时，完成项目内代码、配置契约、生命周期和离线测试即可，不自动连接真实 NAS。
- 用户明确要求接通、跑通或真实验证，且环境已安全提供端点和凭据时，先做只读登录/根目录探针，再只验证项目实际需要的下载、预览或写入能力。
- 真实上传、建目录、改名、移动或清理等远程写操作，需要明确的动作、目标范围和覆盖策略；先 dry-run/全量预检，再执行和回读。
- 用户要求端到端时，从项目真实 CLI、HTTP、worker 或迁移入口跑最小样本，不能用 skill 脚本成功代替项目业务链成功。

网络、证书、凭据、DSM 版本或 ACL 等外部条件缺失时，完成仍可离线完成的部分，并准确停在相应证据层级，不能把“代码完成”写成“真实 NAS 已接通”。

## 高频硬约束

- DSM 入口与文件路径分离：`https://host:5001` 是入口，`/volume1/...` 是业务定位符，后者不是公网 URL。
- 本 Skill 资产采用规范 `/volume1/...` 路径并在 File Station 边界转换为 `/<share>/...`，按 `NAS_ALLOWED_ROOT` 校验。目标项目可采用已有的共享根相对路径和 settings 合同；保留其路径、权限及符号链接检查，不强制改为绝对路径或增加同义配置。
- 密码和真实 `sid` 不进入源码、命令参数、日志、测试夹具、业务 DTO、公共链接、回执或最终答复。DSM 协议所需 `_sid` 可由 adapter 放入内部 query/form；不能记录含其值的请求 URL。HTTP 异常可能带完整 URL，必须重构并脱敏。
- JSON API 同时检查 HTTP 状态、JSON 对象、`success is true` 和上下文对应的业务码；HTTP 200 不能单独证明成功。
- 本 Skill 上传资产默认不覆盖；目标项目已有业务写入策略优先，不以通用建议覆盖用户确认的同名覆盖合同。新增批量流程按其实际副作用和幂等策略确定预检范围；不把某业务目录的覆盖特例推广到其他目录。
- 只读请求可有限重试；写请求超时或断连先读取源/目标真值表。证据不唯一时进入 `NasOutcomeUnknown/reconciliation_required`，不能盲目重放。
- `CopyMove.status` 的 `finished=true` 仍需源/目标对账；Range/流式响应的完成点包括 response/session 正确释放。
- 进程锁只证明单进程互斥。多 worker/多实例写入使用持久队列、原子 claim、唯一幂等约束或分布式锁。
- NAS 返回文件或流，不返回下游平台素材 ID。下游上传必须接收校验后的本地文件/流，并独立检查平台 HTTP 与业务码、保存两段回执。

如果账号密码或 `sid` 泄漏到公开链接、未脱敏异常、日志、终端输出或聊天中，停止继续使用该凭据，指出泄露位置，并要求先在 DSM 轮换。

## 按任务报告结果

- **知识查询/方案**：列出能力入口、原子接口、载荷位置、响应提取/过滤、完整流转、失败与对账语义。
- **代码评审/诊断**：区分代码确认、与参考契约的差异、风险、工程建议和未验证边界。
- **一次性操作**：列出执行的只读/写入范围、非秘密输入、实际回执、产生的本地或远程副作用以及未完成项。
- **项目实施/验证**：列出代码改动、离线测试、真实 NAS 验证、项目端到端验证和未验证项；只报告证据支持的最高层级。

仅在实施或验证分支使用以下状态，不把它们当成每次调用的默认产物：

| 可报告状态 | 必须同时具备的证据 |
| --- | --- |
| 代码接入完成 | 项目内 adapter、配置映射、生命周期和目标项目离线契约测试通过 |
| NAS 只读已接通 | 代码接入完成 + 真实登录/根目录探针；下载需求另有真实小文件证据 |
| NAS 写入已接通 | NAS 只读已接通 + 已授权目录中的预检、写入与回读；高完整性需求另有 SHA-256 回读 |
| 项目端到端已跑通 | 所需读写层级已接通 + 项目真实业务入口最小样本成功 |

未达到某行全部证据时使用上一层状态并逐项列出缺口。dry-run、mock 测试、静态检查或邻近接口成功不能替代更高层级的真实证据。
