# P1-B 文件上传安全

统一上传安全策略与各业务上传端点的接入说明。分为「已实现」（代码已测）与
「部署方需确认」（运行环境/云侧责任）两节。

## 已实现

### 统一校验入口 `secure_spool_file`（app/services/documents/document_security.py）

所有用户上传先经此入口，顺序固定：

1. **内容检测 MIME**：只读文件头 512B，用纯标准库 magic-byte 嗅探判断真实类型，
   与扩展名白名单交叉校验；**不信任客户端 Content-Type**，也不只信扩展名。
   - 文本类（md/txt/csv）：不得命中任何二进制签名（含 NUL 字节检查）。
   - 旧版 Office（doc/xls）：仅接受 OLE2 复合文档魔数。
   - docx/xlsx：必须是 ZIP 容器（OOXML）。
   - 不匹配 → `DOCUMENT_MIME_MISMATCH` / `DOCUMENT_TYPE_NOT_ALLOWED`。
2. **流式落盘 + 单文件大小上限**：分块读取并边算 SHA-256，禁止整体 `read()`；
   超限 → `DOCUMENT_TOO_LARGE`。临时文件带**真实扩展名**（部分解析库如 openpyxl
   按扩展名判定格式）。
3. **zip-bomb 防护**（docx/xlsx）：只读 ZIP 中央目录元数据，**不实际解压**；
   检查条目数 / 总解压大小 / 单条目压缩比 / 嵌套归档 / 加密 / 未知压缩算法
   （配置见下）。任一超限 → `DOCUMENT_ZIP_BOMB` 等。
4. **病毒扫描**：默认 `NoopVirusScanner`——**明确标注"未配置扫描器"，不伪造扫描
   通过**；`DOCUMENT_VIRUS_SCAN_ENABLED=true` 时使用 ClamAV（clamd），扫描器不可用
   即 fail-closed 拒绝，检出病毒 → `DOCUMENT_VIRUS_FOUND`。

文件成功落临时盘后由调用方负责清理；校验失败时入口自清理且不留残留。

### 接入的端点（全部走统一入口）

| 端点 | 扩展名白名单 | 备注 |
|---|---|---|
| `POST /api/documents/upload` | `DOCUMENT_ALLOWED_EXTENSIONS` | 主文档上传，含 DLP/权限快照 |
| `POST /api/documents/batch-upload` | 同上 | **新增批量总大小上限** `DOCUMENT_MAX_BATCH_TOTAL_MB`，逐文件按流长度累计，超限整体拒绝 |
| `POST /api/legal/sources/import` | csv/xlsx/xls | 法源导入（改：原整体 `read()` + 仅扩展名检查 → 统一入口） |
| `POST /api/legal/contract-reviews/upload` | pdf/docx/doc/txt/md | 合同审查（改：同上） |
| `POST /api/channels/drafts/{id}/attachments` | `DOCUMENT_ALLOWED_EXTENSIONS` | 邮件附件，另含 MAILBOX_ATTACHMENT MIME 白名单 + DLP |

### 服务端文件名与存储

- 数据库只存 `object_key`（`users/{uid}/docs/{doc_id}/v{n}/{uuid}.{ext}`），
  叶名为服务端生成的 UUID，**用户文件名永不参与路径拼接**。
- `LocalStorageAdapter._path` 对 key 做 `resolve + is_relative_to` 校验，越界抛
  ValueError（路径穿越防护）。
- **最小权限**：本地存储写入后文件 `chmod 0600`、目录 `0700`（POSIX；不可执行）。
- 上传拒绝（伪造 MIME / 超限 / zip-bomb / 病毒 / 类型不允许）写安全审计事件
  （event_type=`document_upload`, result=`blocked`, reason_code=错误码），**sanitized_metadata 只含文件名/扩展名/大小/错误码，不落
  文件内容或文件头**；审计写失败按 `security_audit_service` 降级策略处理，不阻断拒绝本身。

### 配置（app/core/config/storage.py）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DOCUMENT_MAX_UPLOAD_MB` | 50 | 单文件上限 |
| `DOCUMENT_MAX_BATCH_TOTAL_MB` | 200 | 批量总上限（P1-B 新增） |
| `DOCUMENT_ALLOWED_EXTENSIONS` | pdf,docx,xlsx,md,txt,png,jpg,jpeg,bmp,webp | 主文档白名单 |
| `DOCUMENT_VIRUS_SCAN_ENABLED` | false | 默认不扫描（Noop 明示），生产开启 |
| `DOCUMENT_CLAMAV_SOCKET` | /var/run/clamav/clamd.ctl | clamd unix socket |
| `DOCUMENT_ZIP_MAX_ENTRIES` | 500 | zip-bomb 条目数 |
| `DOCUMENT_ZIP_MAX_TOTAL_UNCOMPRESSED_MB` | 200 | 解压总大小 |
| `DOCUMENT_ZIP_MAX_COMPRESSION_RATIO` | 1000.0 | 单条目压缩比 |
| `DOCUMENT_ZIP_MAX_NESTING` | 2 | 嵌套归档层数 |

## 部署方需确认

1. **生产必须开启病毒扫描**：`DOCUMENT_VIRUS_SCAN_ENABLED=true` + 部署 ClamAV，
   并确认 `DOCUMENT_CLAMAV_SOCKET` 可达；未开启时系统**只做"未配置"降级**，不宣称
   已扫描（见默认策略）。扫描粒度建议叠加云原生/EDR 文件扫描。
2. **对象存储桶策略**：切换 `STORAGE_PROVIDER=minio|s3|oss` 时，桶 ACL/策略需
   部署方配置为**私有、不可公开读、不可执行**（本库本地实现保证 0600/0700，
   云侧由桶策略负责，见 `docs/CONFIG.md` 存储节）。
3. **大小/压缩比阈值**：按业务实际文件分布复核默认值（50MB/200MB/1000x/500 条），
   值调小属收紧；调大需评估 DoS 风险。
4. **临时目录**：spool 使用系统临时目录，部署方需确保其位于只读隔离卷、容量受控，
   并纳入备份/清理策略（`data/uploads` 之外的临时文件生命周期）。
5. **zip-bomb 审查只读元数据**：中央目录本身可能被攻击者伪造（如声明极小体积），
   `[Content_Types].xml` 真实性未逐项校验；如需更强保证，可后续增加逐条目预览。

## 测试覆盖（tests/test_upload_security_p1b.py + tests/test_document_security.py）

伪造 MIME（PNG→.pdf / PDF→.csv / 非 OLE2→.xls）、单文件超限、批量总超限、
危险扩展名（.exe/.sh）、object_key 路径穿越、压缩炸弹（条目/压缩比/嵌套/加密）、
扫描器不可用 fail-closed、mock 检出病毒、拒绝审计不含文件内容、存储 0600/0700。