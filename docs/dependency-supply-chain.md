# P1-E 依赖供应链安全 / SBOM / 门禁

后端依赖的**可复现安装、漏洞门禁、SBOM 与许可证可审计性**。核心原则与既有
P1 一致：**默认拒绝/最小权限**，任何放宽（漏洞豁免、跳过哈希）都必须显式配置、
可审计、有截止日期。本文档分「已实现」与「部署方需确认」两节。

## 已实现

### 1. 锁文件 + 哈希（可复现安装）

- `requirements.lock` / `requirements-dev.lock`：**完整解析的精确版本 + SHA-256
  哈希**，由 `uv pip compile` 生成（见下「锁文件再生成」），提交入库。
- 安装侧全部强制 `--require-hashes`（有哈希才装，防投毒/篡改）：
  - CI 五个 job（backend-tests / lint / contract-check / ws-protocol-check /
    eval-regression / supply-chain）均为
    `pip install --require-hashes -r requirements.lock -r requirements-dev.lock`；
  - `Dockerfile` 为 `pip install --no-cache-dir --require-hashes -r requirements.lock`。
- 锁文件针对 **Linux / Python 3.11** 解析（与 CI、Docker 目标一致），在 Windows
  本地**不用锁文件安装**（本机解释器为 py3.13，与锁定版本集不一致），开发仍用
  `requirements.txt`/`requirements-dev.txt`；锁文件是 CI/部署的安装契约。

### 2. 漏洞门禁（fail-closed）

`scripts/dependency_scan.py`：

- 基于 `requirements.lock` 运行 `pip-audit --no-deps --format json`（**对锁定版本
  扫描，不扫描当前环境**），`--disable-pip` 禁用索引刷新，保证可离线复现。
- 默认 `--fail-on critical,high`；pip-audit 报告无 CVSS 严重度的漏洞**一律按失败
  处理**（fail-closed，不静默放行未知严重度）。
- 豁免必须写入 `scripts/dependency_exemptions.json`（按漏洞 ID）：
  ```json
  {
    "CVE-xxxx-xxxxx": { "reason": "等待上游修复", "until": "2027-01-01", "who": "owner" }
  }
  ```
  - 必须含 `reason` 与 `until`；**`until` 过期即按失败处理**（强制续期或修复），
    豁免只针对指定漏洞、不豁免整个包；
  - 当前豁免清单为空（`dependency_exemptions.json`），即**默认零豁免、全部门禁**。
- 退出码：`0` 通过（含豁免警告）/ `1` 门禁失败 / `2` 工具或配置错误（如锁文件缺失、
  豁免 JSON 非法）。

### 3. SBOM / 许可证 / 过期报告

`scripts/dependency_report.py`，产物输出到 `sbom/`：

- `sbom/backend-cyclonedx.json`：CycloneDX SBOM，**直接由 `requirements.lock`
  生成**（与 CI/部署实际安装的版本集一致，而非范围约束的 requirements.txt）；
  随仓库提交（可复现、可审计）。
- `sbom/licenses.json` / `licenses.csv`：`pip-licenses` 当前安装环境许可证清单，
  含 copyleft/传染性许可证标记（GPL/AGPL/LGPL/MPL/EPL/SSPL）；基于当前环境生成，
  **不提交**（见 `.gitignore`），CI 在锁定环境内生成即与锁文件一致。
- `sbom/outdated.json`：`pip list --outdated` **仅报告、不设门禁**（升级节奏是
  业务决策，见「部署方需确认」）。
- 报告产物**不含任何密钥、环境配置、业务数据**，仅包名/版本/许可证/漏洞元数据。

### 4. CI 供应链门禁（job: supply-chain）

`.github/workflows/ci.yml` 新增 `supply-chain` job，四步：

1. **锁文件新鲜度**：`uv pip compile --no-header --python-platform linux
   --python-version 3.11 --generate-hashes --output-file /tmp/... requirements.txt`
   后 `diff -u` 与提交的 `requirements.lock` 比对——**任何人改了 requirements.txt
   忘记重新编译锁文件，CI 直接失败**；
2. **漏洞扫描**：`python -B scripts/dependency_scan.py`（fail-closed）；
3. **SBOM + 许可证 + 过期报告**：`python -B scripts/dependency_report.py`；
4. **上传 SBOM 工件**：`sbom/backend-cyclonedx.json` 作为 CI artifact（`if-no-files-found:
   error`），供入库登记/扫描平台消费。

### 5. 锁文件再生成（本地与 CI 同命令）

统一使用 `uv`（已加入 `requirements-dev.txt` 锁定 `uv==0.11.32`，与 pip-audit 等
工具一起随 dev 锁安装）：

```bash
# 主锁文件（Linux / py3.11，与 CI/Docker 一致）
uv pip compile --no-header --python-platform linux --python-version 3.11 \
  --generate-hashes -o requirements.lock requirements.txt

# dev 锁文件（含主依赖 + dev 工具）
uv pip compile --no-header --python-platform linux --python-version 3.11 \
  --generate-hashes -o requirements-dev.lock requirements-dev.txt requirements.txt
```

> 说明：`--python-platform linux --python-version 3.11` 保证哈希与 CI/Docker
> 实际安装的 wheel 一致；在 Windows 本地机器上**只编译、不安装**锁文件。CI 的
> 新鲜度检查使用同一组参数，任何参数漂移都会在 diff 步骤暴露。

## 部署方需确认

1. **首次接入 CI**：确认 GitHub Actions 的 Python 3.11 缓存策略
   （`cache: pip` 对 `--require-hashes` 安装同样生效）；Docker 构建需能访问
   PyPI（或内网镜像），因为 `--require-hashes` 安装仍从索引下载。
2. **漏洞豁免流程**：出现新漏洞时，先尝试升级修复；确需豁免才写
   `dependency_exemptions.json`，并设**不晚于下一季度的 `until`**，到期由 CI
   强制提醒续期或修复。豁免记录（who/reason/until）是审计证据，随代码评审提交。
3. **升级节奏**：`sbom/outdated.json` 仅报告不门禁——依赖升级属业务风险决策，
   建议在例行安全评审中按「安全修复 > 小版本 > 大版本」排序处理；每次升级后
   重新生成锁文件并跑全量回归。
4. **SBOM 消费**：`sbom/backend-cyclonedx.json` 可接入漏洞管理平台
   （如 Dependency-Track / DefectDojo / 自建扫描器）；若部署产物与锁文件分叉
   （如自建镜像叠加依赖），需额外对镜像产物出 SBOM。
5. **本地开发约束**：Windows/py3.13 本地环境不安装锁文件；如需在本机跑
   pip-audit/SBOM 验证，直接执行上述脚本即可（pip-audit 读锁文件、SBOM 读锁
   文件，均不依赖本机安装集；许可证/过期报告才会反映本机环境）。

## 测试与验证

供应链门禁本身是**构建期/CI 校验**（非单元测试），已在本机验证：

- `python -B scripts/dependency_scan.py` → 退出码 0（0 漏洞、0 门禁失败）；
- `python -B scripts/dependency_report.py` → 退出码 0，生成
  `sbom/backend-cyclonedx.json`（174 个锁定组件）、`sbom/licenses.*`、
  `sbom/outdated.json`；
- 锁文件新鲜度：`uv pip compile` 重编译后与 `requirements.lock` diff 为空；
- 既有全量回归测试不受影响（本阶段无代码路径改动，仅新增 CI/脚本/Dockerfile）。
