# 角色模板（roles/）

> 五个具名 Codex 角色模板：routine/high × advisor/verifier，由 SKILL.md 路由规则引用。

## 概述

`roles/` 下五个 TOML 文件是 Codex agent 配置模板。`SKILL.md` 的路由规则按名字引用它们；`tools/install.py --roles` 把它们复制到 `<codex-home>/agents/`。每个文件带 `sol-*-managed:v1` 标记，便于将来批量升级或检测漂移。

角色注册依赖目标机器现有的 agent 配置机制；安装器 **不** 改写 `config.toml`。启用技能前需确认 Codex 环境能调用全部五个角色名。

## 关键文件

| 文件路径 | 角色名 | 模型 | reasoning |
|---------|--------|------|-----------|
| `roles/sol-advisor-routine.toml` | `sol_advisor_routine` | `gpt-6-luna` | `xhigh` |
| `roles/sol-advisor-standard.toml` | `sol_advisor_standard` | `gpt-6.1-terra` | `high` |
| `roles/sol-advisor-high.toml` | `sol_advisor_high` | `gpt-6.1-sol` | `high` |
| `roles/sol-verifier-routine.toml` | `sol_verifier_routine` | `gpt-6-luna` | `xhigh` |
| `roles/sol-verifier-high.toml` | `sol_verifier_high` | `gpt-6.1-sol` | `high` |

## 核心概念

### Worker 角色（advisor）
实现工作。`developer_instructions` 强调：在已确定的架构内做有界、良定义、机械性的工作；保留已定架构、owned files、interfaces、并发编辑；跑请求的检查并报告证据。high 角色额外处理复杂/安全敏感/算法/调试/大爆炸半径工作，并要求 surface ambiguity。

### Verifier 角色（verifier）
**严格只读**。`developer_instructions` 明确：不得写或编辑源码；独立运行 build/test/lint、检查 git status/diff vs worker 的 assigned scope、确保无回归或越界编辑；返回 <30 行结构化 Markdown 报告（`VERDICT/TESTS_STATUS/SCOPE_CHECK/DEFECT_LIST`，high 还要 `RISK_ASSESSMENT`）。

### 模型分层
- routine：`gpt-6-luna` / `xhigh` —— 低风险的明确任务及常规验证。
- standard：`gpt-6.1-terra` / `high` —— 普通软件工程的默认 Worker。
- high：`gpt-6.1-sol` / `high` —— 复杂跨系统诊断、高风险实现与独立验证。

### 与 SKILL.md 的关系
- Section 1 路由规则按名引用这四个角色。
- Section 2 worker 委托契约适用于两个 advisor 角色。
- Section 3 验证门适用于两个 verifier 角色。
- 内置 advisor/reviewer 角色禁用；只允许这四个具名角色。

## 工作流程

```
SKILL.md 路由 → 选定角色名
  ↓
spawn subagent(角色名, fresh context, prompt)
  ↓
Codex 加载 <codex-home>/agents/<角色名>.toml
  ↓
按 model + model_reasoning_effort + developer_instructions 执行
```

## 配置

| 字段 | 说明 |
|------|------|
| `name` | Codex 角色名（下划线形式） |
| `description` | 角色描述 |
| `model` | 模型 ID |
| `model_reasoning_effort` | reasoning 强度 |
| `developer_instructions` | 角色约束与职责说明 |

## 注意事项

- 安装器只 **复制** 模板到 `<home>/agents/`；不修改 `config.toml`，不注册角色。注册依赖目标机器的 Codex 配置。
- 若目标机器已有同名文件且字节相同 → 跳过，避免无意义备份。
- 旧文件同样备份到 `<home>/magent-install-backups/<stamp>/agents/<name>`。
- `sol-*-managed:v1` 注释行是版本标记，便于将来检测模板漂移。
- high 角色的 `model_reasoning_effort` 是 `high` 而非 `xhigh`：high 模型本身能力更强，reasoning effort 适度即可；routine 模型用 `xhigh` 补偿。
- 修改这些模板会直接影响所有使用该角色的调用；改前考虑版本号或向后兼容。

---

**相关页面**: [skill-routing.md](skill-routing.md) | [verification-gate.md](verification-gate.md) | [install-sync.md](install-sync.md) | [overview.md](overview.md)
> 安装器复制角色文件但不注册 config.toml。运行 `python3 tools/check_role_registration.py --home ~/.codex` 检测注册缺项；绝不自动编辑用户配置。
