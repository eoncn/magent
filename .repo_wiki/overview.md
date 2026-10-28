# 项目总览

> `magent` 是一个 Codex skill，实现能力路由的多 Agent 软件交付流程。

## 概述

本仓库根目录即 Codex 技能目录，`SKILL.md` 是技能入口与路由规范。`magent` 让一个 Lead Coordinator 在面对任务时：先分类任务难度，再委托给合适层级的 worker subagent 实现，然后委托给独立的 verifier subagent 验证，最后可选地发起外部 Claude/CCB 审核。整个流程的目的是让"实现 - 验证 - 审核"三步在模型与上下文上彼此独立，避免单点自我背书。

源码在 Linux、macOS、Windows 三平台共享同一分支。运行时状态（审核运行目录、日志、回调状态）保存在仓库 **外部** 的 `MAGENT_REVIEW_RUNS_DIR` 或 `${CODEX_HOME}/magent-runs`（共享检出默认 `~/.codex/magent-runs`）。仓库本身不含 CCB、Codex 凭据或生产任务状态。

## 关键文件

| 文件路径 | 说明 |
|---------|------|
| `SKILL.md` | 技能入口、路由规则、验证门、外部审核协议（Section 1-4） |
| `README.md` | 跨平台维护说明、安装与同步命令、离线验证入口 |
| `CLAUDE.md` | Claude Code 工作指引，含 Wiki 必读章节 |
| `references/external-review.md` | 外部 CCB 审核生命周期、状态/回调协议、Windows 执行细节 |
| `scripts/claude-review.sh` | 外部审核 Bash 入口，opt-in 检查与同步模式 |
| `scripts/review_runner.py` | Python 监督器，后台审核生命周期 |
| `scripts/review_support.py` | 仓库指纹（HEAD + 索引 + 跟踪/非忽略内容 + 子模块） |
| `scripts/review_result.py` | CCB 输出 envelope/JSON schema/verdict 一致性校验 |
| `scripts/review_windows.py` | Windows Job Object / 字节范围锁 / 进程身份 |
| `tools/install.py` | 把本检出链接进 Codex home（POSIX symlink / Windows junction） |
| `tools/sync.py` | 通过 SSH 把已配置远端检出快进到本地 HEAD |
| `roles/*.toml` | 五个具名角色模板（routine/high × advisor/verifier） |

## 核心概念

### Skill 而非应用
仓库根是技能目录。安装就是把 `~/.codex/skills/magent` 链接到这里。没有 `package.json`、没有构建产物、没有运行时依赖装在仓库里。Python 3.9+ 是唯一运行时要求；Windows 额外需要 Git Bash 与原生 CPython 3.12+。

### 三步独立
实现者、验证者、外部审核者各自在独立上下文运行。父会话（Lead Coordinator）不直接跑 build/test/lint，不读原始日志，只读验证器返回的 <30 行 Markdown 报告。这把"信任谁"的问题从"信任实现者"转移到"信任一个独立验证者"，并保留对外部审核的显式 opt-in。

### 默认关闭外部审核
外部 Claude/CCB 审核默认禁用。唯一启用方式是技能调用的首个参数为字面量 `--claude-review`。`SKILL.md` 与 `claude-review.sh` 都在入口处强制检查这一点；放错位置或未知标志都保持禁用。独立 verifier 是默认且充分的验收门。

### 状态外置
所有审核运行目录、`state.json`、`packet.txt`、`progress.log`、`stdout.json`、`result.json`、`supervisor.log` 都在 `${CODEX_HOME}/magent-runs/<run_id>/` 下。`.gitignore` 排除 `magent-runs/` 与 `*.log`。共享检出时建议显式设置 `CODEX_HOME`，让两套 Codex 各自保留运行状态。

## 工作流程

```
用户 → $magent [--claude-review] 任务描述
  ↓
Lead Coordinator: 分类难度 (Section 1)
  ↓
solo | delegate → sol_advisor_{routine,standard,high}
  ↓
独立 verifier: sol_verifier_{routine,high}  ← Section 3 验证门
  ↓
PASS? → (默认) 接受
       (opt-in) → 外部 CCB 审核 (Section 4) → verifier 再次验证 → 接受
FAIL? → 带缺陷反馈重新委托 worker → 新 verifier 报告
```

## 配置

| 环境变量 | 默认 | 作用 |
|---------|------|------|
| `CODEX_HOME` | `~/.codex` | Codex home；运行状态默认 `$CODEX_HOME/magent-runs` |
| `MAGENT_REVIEW_RUNS_DIR` | `$CODEX_HOME/magent-runs` | 显式覆盖审核运行根目录 |
| `CLAUDE_CCB_BIN` | `ccb` | CCB 启动器路径 |
| `CLAUDE_CODEX_BIN` | `codex` | Codex 启动器路径（用于 `queue --thread --message`） |
| `CLAUDE_REVIEW_MODEL` | `opus` | CCB 审核模型 |
| `CLAUDE_REVIEW_EFFORT` | `high` | CCB 推理强度（low/medium/high/xhigh/max） |
| `MAGENT_REVIEW_SESSION_ID` | 随机 UUID | 持久 CCB 会话 ID |
| `MAGENT_REVIEW_RESUME` | `0` | 设为 `1` 时传 `--resume` 而非 `--session-id` |

## 长任务支持（Phase 2）

主 Agent 在里程碑、上下文压缩前、故障恢复时调用 `scripts/task_state.py`；状态置于 `$CODEX_HOME/magent-tasks`，与代码仓库及 CCB 运行目录分离。状态通过 revision 防过期覆盖，要求单 writer，Verifier PASS 才能 close。Verifier FAIL 的同缺陷定向修复最多一次，随后 Luna→Terra→Sol 递进，六次修复上限，最终失败报告阻塞。参考 `docs/three-stage-implementation.md`。

## 注意事项

- 只在本仓库改源码。提交推送后用 `tools/sync.py` 更新远端，不要在远端直接编辑。
- 回退版本前先结束在飞的外部审核，避免后台进程跨版本加载脚本。
- `roles/*.toml` 中的角色需由目标机器的 Codex 环境实际可调用；安装器不修改 `config.toml`。
- Windows 必须使用 Git Bash，不能用 System32 Bash/WSL 启动器。

---

**相关页面**: [skill-routing.md](skill-routing.md) | [verification-gate.md](verification-gate.md) | [external-review.md](external-review.md) | [install-sync.md](install-sync.md) | [cross-platform.md](cross-platform.md) | [testing.md](testing.md) | [index.md](index.md)