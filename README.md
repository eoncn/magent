# magent 源码与跨平台维护

这是 Linux、macOS、Windows 共用的 magent 源码。仓库根目录本身就是技能目录。
以 home-server 的最新职责划分、独立内部验收和外部审核默认关闭规则为基线，
合入 CCB 后台运行、持久会话、停止、恢复、超时、回调及 Windows 原生适配。
外部审核调用 `ccb`；`claude-review.sh` 是保留兼容名称，不调用独立的 `claude` 命令。

## 目录

- `SKILL.md`：技能入口和路由、内外审核规则。
- `scripts/`：跨平台审核实现及离线回归测试。
- `references/external-review.md`：审核参数、状态和回调协议。
- `roles/`：从 home-server 收录的五份 worker/verifier 配置模板。
- `tools/install.py`：将同一台机器的技能入口链接到本仓库。
- `tools/sync.py`：通过免密 SSH 更新已配置好的远端 Git 检出。

## 首次接入 Git

在此目录初始化、提交并配置你自己的远程仓库。远端机器首次检出到
`~/otherWorks/magent`；Windows 的 `~` 对应用户配置目录。三台机器使用同一发布分支，
并设置该分支的 upstream。Git 初始化、提交和推送由维护者执行。

## 本机安装

需要 Python 3.9+；Windows 使用原生 CPython 3.12+，审核另需 Git Bash。
Windows 安装器通过 CPython 的 junction 接口建立目录链接，无需符号链接权限。

```bash
cd ~/otherWorks/magent
python3 tools/install.py --dry-run
python3 tools/install.py
```

默认把 `~/.codex/skills/magent` 和 `~/.codex-us/skills/magent` 链接到本目录。
旧目录移至各自 Codex home 的 `magent-install-backups/<时间戳>/skills/magent`，
同一次安装出错会回滚已替换入口。重复安装不会重复备份正确链接。
自定义安装位置可以重复指定 `--home /path/to/codex-home`。

`roles/` 保留五个具名角色及其模型配置。若目标机器缺少这些配置，可运行
`python3 tools/install.py --roles` 安装模板；旧文件同样备份。
角色注册沿用每台机器现有的 agent 配置机制；安装器不改写 `config.toml`。
新模型：`sol_advisor_standard` = `gpt-6.1-terra` / `high`；已有四个角色保留。
请使用 `python3 tools/check_role_registration.py --home ~/.codex` 检查文件和手动注册结果。
在启用新版技能前，确认 `sol_advisor_routine`、`sol_advisor_standard`、`sol_advisor_high`、
`sol_verifier_routine`、`sol_verifier_high` 都可由当前 Codex 环境调用。

审核日志、会话和回调状态保存在 Codex home，路径和凭据通过本机环境变量提供。
共享检出时建议各启动入口明确设置 `CODEX_HOME`，使两套 Codex 各自保留运行状态。
未设置时共享源码默认使用 `~/.codex/magent-runs`，不会写入本仓库。
`MAGENT_REVIEW_RUNS_DIR` 可显式覆盖该路径；不要在任务运行期间改变它。

## 日常修改和同步

只在本仓库修改源码。检查通过后提交、推送，再从该仓库执行：

```bash
python3 tools/sync.py --host macos-server --host home-server --dry-run
python3 tools/sync.py --host macos-server --host home-server --install
```

同步工具要求本地和远端工作区干净；通过 upstream 获取代码，仅允许快进到
本地 HEAD 对应的同一提交。远端 upstream 版本不一致、分支分叉或有未提交修改时停止。
首次执行 `--install` 建立链接，之后 Git 更新就会由两套技能入口共同使用。
同步按主机依次执行，某台失败时停止；已成功的主机保留更新，重跑可继续。
不自动克隆、提交、推送或覆盖本地修改。

版本回退也在 Git 仓库操作；入口仍链接到该检出。回退前先结束现有外部审核任务，
避免后台进程跨版本加载脚本。原安装备份可用于手工恢复入口。

## 长任务检查点和角色检测

主 Agent 对跨阶段任务可运行 `python3 scripts/task_state.py init --repo /path/to/project --objective "任务" --scope "范围" --baseline <sha>`，通过 `checkpoint/show/list/close` 维护状态。检查点位于 `$CODEX_HOME/magent-tasks`（或 `MAGENT_TASKS_DIR`），不放进业务 Git。单个任务仅允许主 Agent 写入，更新时必须指定 `--expected-revision`；只有独立验收通过且无待办时才能 close。使用范例与第三阶段本地评测见 [三阶段实施计划](docs/three-stage-implementation.md)。

## 第三阶段实测复盘（2026-10-28）

40 次有效正式运行（20 个旧/新配对）在操作者报告中均通过；但新增 Terra standard 未在正式 M 类任务被调用，耗时与已知 Token 也未出现改善。**不宣称性能优化收益、不调整现有 Luna/Terra/Sol 路由阈值或 effort**。长任务断点恢复及 routine→standard 故障升级通过了专项真实测试。详见 [第三阶段复盘与保守收敛](docs/stage3-evaluation-2026-10-28.md)。原始 CSV、原始会话日志及本机身份/用量资料不随仓库公开。

## Terra High 定向对照（独立实验，不改正式技能参数）

第三阶段正式 M 类 8/8 都走 routine，不能用于证明 Terra 的正常开发收益。新增 [Terra 标准 Worker 专项实验操作手册](docs/terra-standard-targeted-trial.md)：先从真实中等任务中找到自然路由 standard 的 4 个案例，再在完全相同的 magent 配置里只替换 standard Worker 的 Luna/xhigh 与 Terra/high，对照 4 对、8 次正式执行。

```bash
python3 tools/test_terra_trial.py
python3 tools/prepare_terra_trial.py prepare --source . --out ../terra-trial-homes
python3 tools/prepare_terra_trial.py verify --out ../terra-trial-homes
python3 tools/analyze_terra_trial.py ../terra-trial-results.csv --out ../terra-trial-report.md
```

环境隔离工具不复制登录凭据、不启动模型请求。试验并不改动现有 roles 模板、SKILL.md 或 CCB。资料在 `docs/terra-trial-*-template.csv`；模型实际运行、子 Agent 路由和验收需本机执行，并按真实记录填写 CSV。**不得提前声明性能提升。**

## 离线验证

从本目录执行，CCB 和 Codex queue 均使用模拟命令，不发送模型请求或真实回调：

```bash
python3 tools/test_maintenance.py
python3 tools/test_skill_config.py
python3 scripts/test_task_state.py
python3 tools/test_stage3_analyzer.py
python3 scripts/test_review_runner.py
# Linux/macOS：同步审核套件使用 POSIX 文件系统夹具
python3 scripts/test_sync_review.py "$PWD/scripts/claude-review.sh"
```

完整生命周期协议见 [external-review.md](references/external-review.md)。本目录不包含
CCB 本身、Codex 凭据或生产任务状态。
