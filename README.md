# magent — Codex 多 Agent 编程协作技能

**magent** 是面向 [OpenAI Codex CLI](https://developers.openai.com/codex/) 的工程开发 Skill。它由**主 Agent（Lead）持续统筹任务**，根据任务复杂度选择不同模型的 Worker 实施修改，再交给**独立、只读的 Verifier** 检查测试结果和文件范围；适用于多文件开发、问题修复、重构及需要跨会话继续的长任务。

它不是另一个 Codex 客户端，也不是自动执行任务的后台服务；安装后通过 Codex 中的 `$magent` 技能指令使用。外部 Claude/CCB 审核是**显式可选**功能，默认关闭。

- [快速安装到 Codex](#快速安装到-codex)
- [自动注册 Agent 与配置校验](#自动注册-agent-与配置校验)
- [检查安装并开始使用](#检查安装并开始使用)
- [运行机制与模型分工](#运行机制与模型分工)
- [维护、恢复与可选审核](#维护恢复与可选审核)

## 核心功能

- **主 Agent 维持长任务**：统一管理需求、架构、文件 ownership、Worker 派发、验收结论和下一步计划，减少长任务在多个子 Agent 之间失去上下文的风险。
- **按能力分层**：清晰、低风险工作使用 Luna；一般多模块开发优先 Terra；跨系统未知根因和高风险问题交给 Sol。小型单文件修改允许由主 Agent 直接实现，但仍要独立验收。
- **独立验证门**：Worker 实现后，由新的只读 Verifier 检查构建/测试、`git diff`、修改范围与风险；不能仅凭 Worker 自报成功就判定通过。
- **有界失败升级**：同类缺陷定向返工，并按 Luna → Terra → Sol 逐级升级；每个里程碑最多六次修复尝试，避免无止境重试。
- **跨会话检查点**：多阶段任务可使用 `scripts/task_state.py` 在 Codex home 保存进度，在新会话中读取任务 ID 继续；它保存状态，**不自动唤醒或后台运行 Codex**。
- **外部审核按需开启**：只有明确写出 `$magent --claude-review ...` 才调用已配置的 CCB，不在普通任务中探测或安装 Claude 依赖。

### 运行机制与模型分工

```text
用户任务
   ↓
主 Agent（Lead）：理解需求、规划、路由、维护长任务状态
   ├── 简单明确任务 → sol_advisor_routine（Luna / xhigh）
   ├── 中等工程任务 → sol_advisor_standard（Terra / high）
   ├── 高风险复杂任务 → sol_advisor_high（Sol / high）
   └── 极小单文件任务 → 主 Agent 可直接实现
   ↓
独立只读 Verifier（Luna / xhigh；高风险由 Sol / high）
   ├── PASS → 完成
   └── FAIL → 定向修复 / 有界升级 / 报告阻塞
   ↓
仅在显式指定 --claude-review 时，可额外调用 CCB
```

| Codex 具名角色 | 默认模型 | Reasoning | 作用 |
| --- | --- | --- | --- |
| `sol_advisor_routine` | `gpt-6-luna` | `xhigh` | 目标明确、边界清晰的常规实现 |
| `sol_advisor_standard` | `gpt-6.1-terra` | `high` | 多文件功能、已有模式重构和中等难度调试 |
| `sol_advisor_high` | `gpt-6.1-sol` | `high` | 复杂根因、安全/并发等高风险工作 |
| `sol_verifier_routine` | `gpt-6-luna` | `xhigh` | 普通任务的独立只读验收 |
| `sol_verifier_high` | `gpt-6.1-sol` | `high` | 高风险不变量及复杂变更验收 |

**注意：** 表格描述的是 Worker/Verifier 的默认配置，**不会更改你当前 Codex 主会话的模型**。实际能否调用取决于本机 Codex 版本、账户或自定义 provider 的模型支持情况；角色不可用时按技能规则报告阻塞，不静默换模型。

## 快速安装到 Codex

### 前置要求

- 已安装并能正常登录 **Codex CLI**，且当前版本支持 Skill 和具名子 Agent。
- 已安装 **Git**。
- **macOS / Linux**：推荐 Python 3.11+（自带 TOML 解析器）。Python 3.9/3.10 需先安装 `tomli`：`python3 -m pip install tomli`，才能安全自动注册 Agent。
- **Windows**：推荐 Windows 11 + 原生 CPython 3.12+；普通安装不需要 Git Bash，只有使用可选 CCB 审核时才需要 Git Bash。
- 使用上表中模型的账户或 provider 权限。安装程序不会替你获得模型访问权限。

**默认一条安装命令完成三项操作**：创建 Skill 链接、复制五个 Agent 模板、自动注册到 Codex `config.toml`，然后执行只读的配置检查。不会要求手动复制五段 TOML，安装程序也不会检查或改写登录凭据。首次使用仍需确认模型访问权限与 Codex CLI 能力。

### macOS / Linux

在终端执行：

```bash
mkdir -p "$HOME/otherWorks"
git clone https://github.com/eoncn/magent.git "$HOME/otherWorks/magent"
cd "$HOME/otherWorks/magent"

# 预览 Skill、Agent 和 config.toml 的变更，不实际写文件
python3 tools/install.py --home "$HOME/.codex" --dry-run

# 一键安装：Skill 链接 + 5 个角色模板 + 自动注册 config.toml
python3 tools/install.py --home "$HOME/.codex"
```

安装后的主要位置：

```text
~/.codex/
├── config.toml                 # 安装器自动追加五个角色注册段
├── skills/
│   └── magent/                 # 指向 git clone 的仓库（符号链接）
└── agents/
    ├── sol-advisor-routine.toml
    ├── sol-advisor-standard.toml
    ├── sol-advisor-high.toml
    ├── sol-verifier-routine.toml
    └── sol-verifier-high.toml
```

### Windows PowerShell

使用**原生 Windows Python**，不需要开启开发者模式或手工创建符号链接；安装器会用 junction 连接 Skill 目录。

```powershell
New-Item -ItemType Directory -Force "$HOME\otherWorks" | Out-Null
git clone https://github.com/eoncn/magent.git "$HOME\otherWorks\magent"
Set-Location "$HOME\otherWorks\magent"

py -3 tools/install.py --home "$HOME\.codex" --dry-run
py -3 tools/install.py --home "$HOME\.codex"
```

Codex 用户目录通常是 `%USERPROFILE%\.codex`（PowerShell 中为 `$HOME\.codex`）。

### 使用其他 Codex Home

上面仅安装到常用的 `~/.codex`，不会自动安装到其他自定义 `CODEX_HOME`。如果有第二套 Codex，可指定多个目录：

```bash
python3 tools/install.py --home "$HOME/.codex" --home "$HOME/.codex-us"
```

省略全部 `--home` 时，安装器默认处理 `~/.codex` 和 `~/.codex-us`。它会在写入前校验现有 TOML 与角色冲突，更新时对原文件创建带时间戳的备份，失败时回滚本轮已替换内容；**不会清空或重写你的 provider、审批、模型、MCP 或其他自定义设置**。

> 如果已有同名角色指向非 magent 路径，或者配置 TOML 格式错误、显式禁用多 Agent、`config.toml` 是符号链接，脚本会报告原因并停止，**不会擅自覆盖**。处理冲突后重新执行即可。

## 自动注册 Agent 与配置校验

安装器已经替你在目标 Codex Home 的 `config.toml` 末尾**仅追加缺少的注册段落**。原有正确配置保留原样，重复安装不重复写入；对有冲突的同名 Agent **不自动修复或覆盖**，防止破坏已有项目配置。已有文件发生替换时，旧版本保存在 `<codex-home>/magent-install-backups/<时间戳>/`，中途失败会尝试自动回滚本次安装。

正常情况下**无需手动编辑**。脚本写入的五个注册段落如下，仅用于故障排查或高级用户参考：

```toml
[agents.sol_advisor_routine]
config_file = "agents/sol-advisor-routine.toml"

[agents.sol_advisor_standard]
config_file = "agents/sol-advisor-standard.toml"

[agents.sol_advisor_high]
config_file = "agents/sol-advisor-high.toml"

[agents.sol_verifier_routine]
config_file = "agents/sol-verifier-routine.toml"

[agents.sol_verifier_high]
config_file = "agents/sol-verifier-high.toml"
```

**安装参数说明：**

| 参数 | 效果 |
| --- | --- |
| `--home <目录>` | 指定 Codex Home，可重复传入；默认同时使用 `~/.codex` 与 `~/.codex-us` |
| `--dry-run` | 只预览变更和冲突，不创建任何文件 |
| `--roles` | 兼容旧命令；新版本默认就会安装五个角色和注册配置 |
| `--no-register` | 安装 Skill 和五个角色文件，**不修改** `config.toml`，供已有特殊注册方案使用 |
| `--skill-only` | 仅安装 Skill 链接，保持早期版本的纯链接行为 |

如果自动安装提示角色冲突，应先查看报错中现有 `config_file` 的位置，不要直接覆盖自己长期维护的角色配置；确认后可手动消除冲突再重试。若你管理的 `config.toml` 是指向 dotfiles 的符号链接，可使用 `--no-register`，在实际配置源文件中自行维护注册条目。

### 检查安装并开始使用

回到 magent 仓库，先运行**只读配置检查**：

```bash
# macOS / Linux
python3 tools/check_role_registration.py --home "$HOME/.codex"
```

Windows PowerShell：

```powershell
py -3 tools/check_role_registration.py --home "$HOME\.codex"
Test-Path "$HOME\.codex\skills\magent\SKILL.md"
```

角色注册通过时会输出类似：

```text
PASS: 5 magent roles installed and registered in ...
```

检查器只确认五个配置文件、模型参数与注册路径正确；**不发送真实模型请求**，也不能证明你的账号有权限调用它们。

完成后重开一个 Codex 会话，在**要处理的业务仓库**而非 magent 源码仓库中执行：

```bash
cd /path/to/your/project
codex
```

在 Codex 输入框中输入：

```text
$magent 修复订单处理中的重复事件问题。先分析当前实现，
明确允许修改的文件与验收命令，再按技能流程实现并独立验证。
```

也可用 `codex exec` 执行（示例中单引号用于避免 Shell 展开 `$magent`）：

```bash
codex exec '$magent 修复这个仓库的一个确定性 bug，并由独立 Verifier 验收'
```

建议先以小型、可执行测试的任务做首次验证；查看最终回答中的路由选择、具名 Worker 和独立 Verifier 报告。**检查通过但实际派发失败**时，应检查 Codex 版本、模型权限、当前 `CODEX_HOME` 和项目级配置覆盖，而不是绕过验收门。

## 日常使用与高级功能

### 长任务检查点

对多阶段任务，主 Agent 可在里程碑或会话压缩前使用 `scripts/task_state.py` 写入任务状态。默认保存到 `CODEX_HOME/magent-tasks`（未设置时为 `~/.codex/magent-tasks`），也可由 `MAGENT_TASKS_DIR` 覆盖。支持 `init`、`show`、`list`、`checkpoint`、`close`；只有独立验收 PASS 且无待办时才能关闭任务。**状态工具不会自动恢复正在运行的进程**。

详细示例：[三阶段实施计划](docs/three-stage-implementation.md)。

### 可选：外部 Claude/CCB 审核

默认只有内部独立 Verifier，**不会自动调用 Claude、安装 CCB 或触发多轮外部审查**。

需要外部审核时，在技能调用中把 `--claude-review` 放在**第一个参数**：

```text
$magent --claude-review 修复当前问题并完成独立审核
```

该功能需要预先配置兼容的 `ccb`、相应命令和回调环境；Windows 还需要 Git Bash。审核可以通过已有线程异步完成，状态、日志保存在 Codex home 或 `MAGENT_REVIEW_RUNS_DIR` 指定目录。具体依赖与恢复机制参见 [外部审核协议](references/external-review.md)。

### 更新与维护

由于 Skill 入口是指向克隆目录的链接，拉取最新代码即可更新 `SKILL.md` 等源码；**角色 TOML 是复制的文件**，升级角色模板时需要重新运行 `install.py`，安装器会同时检查并补齐缺失的注册信息（已有有效配置不变）。

```bash
cd "$HOME/otherWorks/magent"
git pull --ff-only
python3 tools/install.py --home "$HOME/.codex" --dry-run
python3 tools/install.py --home "$HOME/.codex"
python3 tools/check_role_registration.py --home "$HOME/.codex"
```

Windows 将 `python3` 改为 `py -3`、路径改为本机路径。不要在 CCB 后台审查仍运行时替换其代码；如安装了多套 `CODEX_HOME`，需分别更新角色模板。

仓库还提供 `tools/sync.py`，供已建立 SSH 和 upstream 的多台机器进行**只允许 fast-forward** 的代码同步，参见 [.repo_wiki/install-sync.md](.repo_wiki/install-sync.md)。它不会替你克隆、提交或推送。

### 离线回归

以下脚本验证安装、任务状态、内部逻辑及模拟的外部审核生命周期；运行它们不会调用真实模型：

```bash
python3 tools/test_maintenance.py
python3 tools/test_auto_install.py
python3 tools/test_skill_config.py
python3 scripts/test_task_state.py
python3 tools/test_stage3_analyzer.py
python3 tools/test_terra_trial.py
python3 scripts/test_review_runner.py

# Linux / macOS：POSIX 文件系统相关回归
python3 scripts/test_sync_review.py "$PWD/scripts/claude-review.sh"
```

## 项目结构

| 路径 | 用途 |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Codex Skill 入口、派发规范、验收门与有界升级规则 |
| [`roles/`](roles/) | 五个具名 Worker/Verifier TOML 角色模板 |
| [`tools/install.py`](tools/install.py) | 一键安装 Skill、五个角色和 `config.toml` 注册，支持备份/回滚 |
| [`tools/register_agents.py`](tools/register_agents.py) | 安全 TOML 注册规划，校验既有配置，禁止覆盖角色冲突 |
| [`tools/check_role_registration.py`](tools/check_role_registration.py) | 只读检查角色配置和注册 |
| [`scripts/task_state.py`](scripts/task_state.py) | 可恢复长任务的状态检查点 |
| [`scripts/`](scripts/) / [`references/`](references/) | 可选 CCB 外部审核、持久会话和回调协议 |
| [`tools/sync.py`](tools/sync.py) | 多机器 Git fast-forward 同步 |
| [`docs/`](docs/) | 设计/实施计划、历史 A/B 评测及专项实验记录 |

## 已有验证与限制

项目已经分别开展原/新版 magent 的功能 A/B，以及 Terra/Luna 标准 Worker 的小样本专项测试。提交的报告表明，特定测试集在独立验收后通过，也记录了返工、额度中断和用量缺失；**这些数据不支持宣称已实现整体性能或 Token 成本收益**。模型路由的具体收益仍取决于真实项目和任务类型。

有关实验范围和证据边界，请阅读：[第三阶段复盘](docs/stage3-evaluation-2026-10-28.md) · [Terra 专项复盘](docs/terra-trial-evaluation-2026-10-28.md)。

> **安全提醒**：不要把 Codex 登录令牌、`auth.json`、私有 provider 凭据或业务敏感数据提交到本仓库；试验环境与长任务运行状态应保存在仓库外部。
