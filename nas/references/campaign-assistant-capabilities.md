# Campaign Assistant 的 NAS 能力与复用边界

## 证据与适用范围

核对日期：2026-11-05。项目：`D:/projects/campaign-assistan-material-generation`；分支 `feature/material-generation`；提交 `f93187d`。本页基于源码和测试文件的只读检查，不代表本次已运行离线测试、真实 NAS、部署或端到端验证。后续使用先核对当前 checkout 和 owning source。

项目根以下路径均相对该 checkout。不要把本页能力迁移为所有项目的硬约束。

## 复用地图

| 需求 | UI / HTTP | 业务 / adapter | 实际边界 |
|---|---|---|---|
| Logo上传 | `features/studio/BrandLogoPicker.tsx`；`POST /api/v1/studio/logos` | `routers/studio.py:upload_logo`；`inspect_image`；`logo_file_path`；`source.write_file` | PNG/JPEG/WebP，5MB；原字节完整hash命名，同内容返回200，否则201；不是通用文件上传 |
| 品牌搜索与Logo选择 | `GET /api/v1/studio/logo-brands`；`GET /api/v1/studio/logos?brand=...` | 同router；目录列举、名称校验、图片读取、解码/hash/扩展名核验、签名 | 已登录用户共享品牌库；品牌搜索服务端有分页；现有Picker未实现继续加载品牌的完整分页交互，不声称UI已消费全部页 |
| 私有主体图片上传 | `POST /api/v1/studio/subjects`；`uploadSubject` | `subject_file_path`；`StudioAssetSigner.sign_subject` | PNG/JPEG/WebP，30MB；`主体图库/{tenant}/{operator}/{hash}.{ext}`；不是公共库 |
| NAS目录浏览与文件选择 | `GET /api/v1/catalog/assets`；`features/reference_pickers/asset/AssetPicker.tsx` | `modules/catalog/service.py:browse_assets` → `modules/assets/service.py:browse_assets` → `ScopedPersonalNasBrowser` | 当前运营个人根只读；文件多选、目录进入、返回上级和分页；没有上传或新建目录UI。POST同路径明确拒绝创建 |
| 精准元数据 | 无通用公开stat API | `adapters/assets/nas.py:DsmClient.metadata` → `_getinfo` | POST `SYNO.FileStation.List/getinfo` v2；不是通过过时注释所述的父目录扫描实现 |
| 文件列举 | 经业务API开放 | `DsmClient.list_children`、`ScopedStudioMaterialSource.list_directory` | List v2/list全分页；scope过滤链接、改道项；Port返回`StudioNasEntry(name,is_directory,size_bytes)` |
| 文件读取 | 经业务API开放 | `ScopedStudioMaterialSource.read_file` → 目录链及文件检查 → `DsmClient.download` | 返回完整bytes，拒绝JSON错误体；不同scope reader不得互相绕过 |
| 文件写入与父目录创建 | Logo、主体和后台结果等专用链 | `StudioMaterialSourcePort.write_file` → scoped adapter → `DsmClient.upload` | Upload v2/upload可带create_parents；无独立CreateFolder实现，无任意路径上传API |
| 图片预览 | `GET /api/v1/studio/assets/{opaque_id}/preview` | session鉴权、签名与owner检查、可信记录定位、source.read_file | 当前是完整读取后按Range切片响应；没有Skill资产的ETag/304直通流式能力，不可混称 |
| 改名/移动/删除/隔离 | 未开放对应业务链 | 当前DsmClient无对应方法；`quarantine_file`明确抛失败 | 不能因Skill资产包含rename/move就说项目支持，不得报告已清理 |

后端入口根：`backend/campaign_assistant/`；前端入口根：`frontend/src/`。

## 实际DSM请求

| 操作 | 方法与载荷 | 返回判断 |
|---|---|---|
| 登录/注销 | POST Auth，form；sid仅保存在client内部 | 检查HTTP与DSM success；会话在应用生命周期共享，失效有单飞刷新，shutdown关闭 |
| list | POST entry.cgi；form含List/v2/list、folder_path、offset、limit、additional、_sid | 全分页且校验分页及条目形状，不能用第一页断言没有文件 |
| getinfo | POST entry.cgi；form含List/v2/getinfo、路径与附加字段 | 验证返回路径、name、isdir等；缺失/错误按真实代码处理，不能把所有408都写成None |
| download | GET entry.cgi；query含Download/v2/download与认证和路径 | 响应释放、大小上限及错误体判断见客户端；项目业务消费bytes |
| upload | POST entry.cgi；`params={_sid}`；form含Upload/v2/upload、path、create_parents、overwrite；`files={file:(filename,content,mime)}` | 检查HTTP和DSM success；响应关闭；不以HTTP200证明成功 |

不要输出完整请求URL、真实sid、密码或配置值。协议内认证参数允许存在于adapter内部，不代表可记录或外露。

## 写入策略不能混用

- `write_file`按第一段目录区分。`生成素材库`走`_upload_generated`：正常仅检查末级目录，允许覆盖，按需create_parents；成功不额外下载回读。异常才按结构化错误和预算核对大小/完整hash，明确安全分支有限重试。
- Logo、主体等非生成目录走`_upload_checked`：目录链与目标检查，`overwrite=exists`；不能称其永不覆盖，也不能称它具备生成路径完整的异常重试/对账链。
- `StudioWriteReceipt.remote_io_uncertain`区分内容确认与旧远端IO结束事实。`ObservedStudioMaterialSource`保持不明观察，不因后续成功洗掉未知。
- 新业务目录必须明确选择适用策略；优先现有Port，不通过目录命名偶然继承特例，不扩大已有写权限。
- 正式结果保存还有`FileStudioResultStaging`、Worker保存预算、PG槽位CAS和确认后清理；单次Upload不是整个任务完成。

相关规范：`.trellis/spec/backend/nas-generated-write.md`、`studio-result-persistence.md`、`logging-guidelines.md`。

## 路径和配置

业务Port使用相对部署共享根的POSIX路径；`normalize_remote_path`在DSM边界处理`/volume1`。权限来自服务端scope、session和资源类型，不来自显示姓名或客户端绝对路径。

当前配置键：`CAMPAIGN_ASSISTANT_NAS_DSM_BASE_URL`、`CAMPAIGN_ASSISTANT_NAS_DIRECTORY_ROOT`、`NAS_USERNAME`、`NAS_PASSWORD`；别名以`config.py`为准。不要照搬Skill示例中的另一套NAS_DSM_URL/NAS_ALLOWED_ROOT。

`main.py:_build_studio_runtime`装配source、Logo resolver、signer、staging和Worker。已有async httpx实现，不迁入同步requests客户端，也不从Skill目录动态import生产代码。

## 证据检查入口

- `tests/adapters/assets/test_nas_session.py`、`test_nas_getinfo.py`、`test_nas_studio_source.py`、`test_nas_generated_write.py`、`test_nas_write_item_error.py`、`test_nas_error_policy.py`。
- `tests/routers/studio/test_studio_sse_and_assets.py`、`test_asset_lifetime.py`、`test_admin_visibility.py`。
- `frontend/tests/component/asset_picker.test.tsx`、`frontend/tests/unit/studio_api.test.ts`。

这些是存在的验证入口，列出它们不等于本次执行通过。Logo模块头部“尚未实现”和某些metadata旧注释与当前实现不一致；不要只凭注释得出能力结论。
