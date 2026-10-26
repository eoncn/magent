# 技能路由（SKILL.md）

> Lead Coordinator 在任何实现工具使用之前的难度分类与路由声明。

## 概述

`SKILL.md` 定义了 `magent` 技能的全部行为：身份、路由规则、worker 委托契约、独立验证门、外部审核协议。它是 Codex 加载技能时读取的入口文件，也是修改路由行为时必须先读的文件。

Lead Coordinator 在动手之前必须显式声明一个 `SELECTIVE ROUTE` 块，写下 `mode`、`target_agent`、`verifier_agent` 与 `reasoning_rationale`。这把"该用哪个模型"从隐式判断变成可审计的文本，并强制在路由时考虑爆炸半径与模糊度。

## 关键文件

| 文件路径 | 说明 |
|---------|------|
| `SKILL.md` | Section 1 路由规则、Section 2 worker 委托契约、Section 3 验证门、Section 4 外部审核 |
| `roles/sol-advisor-routine.toml` | `gpt-6-luna` / `xhigh`，routine 实现 |
| `roles/sol-advisor-high.toml` | `gpt-6.1-sol` / `high`，高难度实现 |
| `roles/sol-verifier-routine.toml` | `gpt-6-luna` / `xhigh`，routine 验证 |
| `roles/sol-verifier-high.toml` | `gpt-6.1-sol` / `high`，高难度验证 |

## 核心概念

### SELECTIVE ROUTE 声明
```text
SELECTIVE ROUTE
mode: solo | delegate
target_agent: none | sol_advisor_routine | sol_advisor_high
verifier_agent: none | sol_verifier_routine | sol_verifier_high
reasoning_rationale: <为什么选这个难度>
```

### 四种路由

| 路由 | worker | 适用场景 |
|------|--------|---------|
| `solo` | 父会话自己实现 | 仅限微小、单文件、contained 编辑；验证仍必须委托 |
| `delegate → sol_advisor_routine` | `gpt-6-luna` / `xhigh` | **默认**。标准实现、重构、单测、bugfix、API、feature |
| `delegate → sol_advisor_high` | `gpt-6.1-sol` / `high` | **严格例外**。复杂数学/加密、微妙多线程并发、无既有模式的破坏性架构迁移 |
| `verifier_agent: none` | —— | 仅用于无实现变更、无验证门的任务 |

### 升级规则
- 多文件范围、公共 API 变更、广泛爆炸半径、一般任务复杂度 **本身不** 构成升级到 `sol_advisor_high` 的理由。
- 选 `sol_advisor_high` 时 `reasoning_rationale` 必须指出具体属于哪一条严格例外，并解释具体原因；否则回退到 routine（或 solo）。
- verifier 选择独立于 worker：默认 `sol_verifier_routine`；`sol_verifier_high` 仅用于数学/加密、并发、金融对账、破坏性架构迁移，且需在 rationale 中点名具体例外。

### Worker 委托契约（Section 2）
每次 worker dispatch prompt **必须** 遵循以下结构：

~~~text
OBJECTIVE
<可验证的清晰结果>

FILES AND OWNERSHIP
You own and may ONLY edit:
- <显式文件/目录路径>
Do NOT modify files outside this list. Preserve concurrent edits.

INTERFACES & CONTRACTS
- <必须遵守的类型/签名/CLI/schema>

CONSTRAINTS
- <性能/向后兼容/不可触碰的范围>

VERIFICATION COMMANDS
- <返回前自检命令>
~~~

### 内置角色禁用
内置 advisor/reviewer 角色保持禁用；只允许上述四个具名角色。这避免模型选择回退到一个未受控的"reviewer"角色。

## 工作流程

1. Lead Coordinator 读任务描述，评估复杂度、爆炸半径、模糊度。
2. 写下 `SELECTIVE ROUTE` 块（含 rationale）。
3. `solo`：父会话自己改代码；`delegate`：spawn 选定 worker subagent，fresh context，按委托契约给 prompt。
4. 进入 [verification-gate.md](verification-gate.md)。

## 注意事项

- `solo` 不跳过验证门：仍必须委托独立 verifier。
- rationale 不可空泛："任务较复杂"不够；需点出具体例外类（如"涉及 RSA 签名验证的侧信道防御"）。
- worker 在 fresh context 中运行：不会看到父会话历史，prompt 必须自包含。
- `FILES AND OWNERSHIP` 是硬约束：worker 不得编辑列表外文件，需保留并发编辑。

---

**相关页面**: [verification-gate.md](verification-gate.md) | [external-review.md](external-review.md) | [roles.md](roles.md) | [overview.md](overview.md)