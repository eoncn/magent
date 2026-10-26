# 独立验证门（Section 3）

> 实现完成后，由独立 verifier subagent 在 fresh context 中执行只读验证，父会话只读其 <30 行报告。

## 概述

`magent` 的核心安全特性是"实现者不验证自己"。Lead Coordinator 在 worker 返回后 **不得** 自行运行 build/test/lint、不得看原始日志、不得自己跑 `git status`/`git diff`。验证总是委托给一个独立的 verifier subagent，它在自己的 fresh context 里独立执行命令、独立检查范围、独立产出结构化报告。父会话只读这份报告。

这把"信任实现者的自报成功"转换为"信任一个独立验证者的可检查证据"。PASS 要求所有适用检查实际跑完；缺证据、命令被阻塞或失败都产出 FAIL。

## 关键文件

| 文件路径 | 说明 |
|---------|------|
| `SKILL.md` | Section 3 定义验证门规则与报告格式 |
| `roles/sol-verifier-routine.toml` | routine verifier 角色模板 |
| `roles/sol-verifier-high.toml` | high-tier verifier 角色模板 |

## 核心概念

### Verifier 的硬约束
- **只读**：不得编辑源码、不得 apply fix、不得跑 autofix 命令、不得改变 Git 状态。
- **独立上下文**：spawn 时 fresh context，不继承 worker 的对话。
- **不可回退**：若选定角色不可用，报告阻塞；**不得** 回退到父会话验证。
- **产物位置**：build/test 产物只能用允许的输出位置；需保留并发工作。

### 父会话的硬约束
- 不得直接跑 build/test/lint。
- 不得直接 inspect 原始日志或证据。
- 不得通过 `git status`/`git diff` 在父会话里验证。
- 只读 verifier 的简洁报告。

### 标准报告格式（<30 行）
```markdown
VERDICT: PASS | FAIL
TESTS_STATUS: <命令/检查、退出码、简明结果；N/A 需说明>
SCOPE_CHECK: <基线、检查范围、任何意外变更>
RISK_ASSESSMENT: <high-tier 必填；不变量发现与残余风险>
DEFECT_LIST:
- <file:line 或失败检查，具体缺陷与期望行为；或 None>
```

### PASS 的条件
- 所有适用检查都成功完成。
- 无未解决缺陷。
- 无范围违规。
- 缺证据、被阻塞/失败的命令、不完整的检查 → FAIL，**绝不** 推断 PASS。
- 父会话用基线区分"预先存在/并发的编辑"与 worker 的改动，不把每个脏文件都算到 worker 头上。

### FAIL 后的循环
1. 父会话把缺陷反馈带给 worker，重新委托。
2. 对受影响的检查重新获取 **新鲜的** 独立 verifier 报告。
3. 环境阻塞也通过委托解决；不重复未变的失败检查，不用父会话手工验证替代。
4. verifier 失败留在这个修复循环里，**不** 自动触发外部审核。

### 与外部审核的衔接
- 默认：verifier PASS → 接受工作（本次未 opt-in 外部审核）。
- opt-in：verifier PASS → 进入 [external-review.md](external-review.md)，把 verifier 的简明结果与退出码填入 `PARENT VERIFICATION EVIDENCE`，**不** 打开原始证据。
- 在外部审核里，"parent verification"指的就是这道委托验证门。

### solo 路由下的验证
`solo` 仍必须委托验证。父会话自己改的代码，也要交给独立 verifier 检查；solo 的修复同样委托给合适的 worker。

## 工作流程

```
worker 返回 → spawn verifier (fresh context)
  ↓
verifier 独立跑 build/test/lint，git status/diff vs 基线，范围检查
  ↓
verifier 返回 <30 行报告
  ↓
父会话只读报告:
  PASS → (默认) 接受 | (opt-in) 外部审核
  FAIL → 带缺陷反馈重新委托 worker → 新 verifier 报告
```

## 注意事项

- verifier 报告里的 `RISK_ASSESSMENT` 仅 high-tier 必填；routine 不要求。
- 报告不得包含原始 log/diff dump；所有内容都已浓缩。
- "worker 自报成功"是 context，不是 proof。
- 失败的 verifier 不会自动升级到外部审核；外部审核只在显式 opt-in 且 verifier 已 PASS 时才启动。

---

**相关页面**: [skill-routing.md](skill-routing.md) | [external-review.md](external-review.md) | [roles.md](roles.md) | [overview.md](overview.md)