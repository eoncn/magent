# review_result.py 输出校验

> 校验 CCB envelope、精确 JSON schema、verdict/finding 一致性，无 jq 依赖。

## 概述

`scripts/review_result.py` 是同步外部审核的最后一道闸。CCB 退出码 0 不代表结果可用：CCB 可能返回事件数组、错误事件、缺 `structured_output`、schema 漂移、verdict 与 finding 矛盾。这个脚本把所有这些情况转成明确的退出码与 stderr 信息，stdout 只在完全合法时打印校验后的 `{verdict, summary, findings}`。

跨平台纯 Python 实现，避免 `jq` 依赖（Windows 通常没装）。`claude-review.sh:199` 在 CCB 退出码 0 且指纹一致后调用它。

## 关键文件

| 文件路径 | 说明 |
|---------|------|
| `scripts/review_result.py` | `validate(value)` 与 `__main__` |
| `scripts/claude-review.sh` | 调用点（同步模式末尾） |
| `references/external-review.md` | schema 与退出码规范 |

## 核心概念

### 输入形式
- **数组**：CCB 事件流。要求非空、全 dict、恰好一个 `type=="result"` 事件；取最后一个。否则退出 31。
- **单事件 dict**：直接校验。

### envelope 校验（退出码 31）
- `type == "result"`
- `subtype == "success"`
- `is_error is False`
- `structured_output` 是 dict

任一不符失败 → `fail("CCB returned no valid successful structured result", 31)`。

### schema 校验（退出码 33）
- `result` 顶层键 **恰好** `{verdict, summary, findings}`（`set(result) != ...` 严格比较，多一个键都失败）。
- `summary` 是 str，`findings` 是 list。
- 每个 finding：键 **恰好** `{severity, file, line, issue, recommendation}`。
- `severity` ∈ `{critical, high, medium, low}`。
- `file/issue/recommendation` 都是 str。
- `line` 是 int 或 null；bool 不算 int；float 必须有限且整数值。

### verdict 一致性（退出码 34）
- `verdict == "ship"` 时 **所有** finding 的 `severity == "low"`（即不允许任何 high/medium/critical finding 与 ship 共存）。
- `verdict != "ship"` 时 `findings` **必须非空**（fix-first/rethink 必须配至少一个 finding 说明问题）。

违反 → `fail("Review verdict contradicts its findings", 34)`。

### verdict 取值（退出码 32）
`verdict` 必须是 str 且 ∈ `{ship, fix-first, rethink}`，否则退出 32。

### JSON 常量防御
`json.load(sys.stdin, parse_constant=invalid_constant)`：拒绝 `NaN/Infinity/-Infinity` 等非标准 JSON 常量，避免 `json` 模块默认接受它们。失败 → 退出 31。

## 工作流程

```
claude-review.sh: CCB 退出 0 + 指纹一致
  ↓
printf '%s' "$raw_result" | python3 review_result.py
  ↓
review_result.py:
  1. json.load (拒绝 NaN/Inf)
  2. 数组? 取唯一 result 事件
  3. envelope: type/subtype/is_error/structured_output
  4. schema: 顶层键集 + 每个 finding 键集 + 类型
  5. verdict 一致性
  ↓
全部通过 → stdout 打印校验后的 {verdict, summary, findings}
任一失败 → stderr "ERROR: ..." + 退出码 31/32/33/34
```

## 退出码表

| 退出码 | 含义 |
|-------|------|
| 31 | CCB envelope 无效 / JSON 无效 / 非法事件数组 |
| 32 | verdict 取值非法 |
| 33 | schema 违规（键集、类型、line） |
| 34 | verdict 与 finding 矛盾 |

## 注意事项

- `set(result) != {"verdict","summary","findings"}` 是严格比较：CCB 多返回一个键就失败。这是故意的：防止 CCB schema 漂移悄悄通过。
- `line` 接受 `null`：finding 不一定有具体行号。
- `line` 是 `float` 时必须 `math.isfinite()` 且 `is_integer()`：拒绝 `NaN`/`Inf`/`1.5`。
- `severity == "low"` 的 finding 可以与 `ship` 共存：低优先级建议不阻断接受。
- `claude-review.sh` 把这个脚本的退出码直接当自己的退出码（`exec`/`exit`）。
- 后台模式不直接调这个脚本：监督器在 `worker()` 里用 `read_json` + `verdict` 取值校验，但 **不** 重复 schema 全检；同步路径是 schema 的权威校验点。这意味着后台模式对 CCB 输出的信任度略高，但 verifier 在 callback 路径会再次独立验证。

---

**相关页面**: [external-review.md](external-review.md) | [review-runner.md](review-runner.md) | [review-support.md](review-support.md) | [testing.md](testing.md) | [index.md](index.md)