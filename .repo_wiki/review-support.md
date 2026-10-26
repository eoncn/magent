# review_support.py 仓库指纹

> 对 HEAD、索引、跟踪内容、非忽略未跟踪内容、子模块取 SHA-256 指纹，用于审核前后变更检测。

## 概述

`scripts/review_support.py` 提供一个纯函数 `fingerprint(directory)`，把一个 Git 工作树的完整可检查状态压成一个 64 字符 hex digest。`claude-review.sh` 在 CCB 运行前后各调用一次；`review_runner.py` 在 attempt 开始与结束各调用一次。两次指纹不一致 → 退出码 30，阻断接受。

这是外部审核的"只读保证"：CCB 被禁用了 `Edit/Write/Bash` 等工具，但仅靠工具白名单不足以防御 host 启动钩子或 CCB 之外的副作用；指纹在进程退出后独立审计仓库实际状态。

## 关键文件

| 文件路径 | 说明 |
|---------|------|
| `scripts/review_support.py` | `fingerprint()` 实现 |
| `scripts/claude-review.sh` | 同步模式前后调用 |
| `scripts/review_runner.py` | 监督器在 attempt 边界调用 |

## 核心概念

### 哈希结构
`hashlib.sha256()`，通过 `record(value)` 把长度（8 字节 big-endian）+ 内容喂入 digest。`record()` 调用顺序固定，所以指纹确定性。

### snapshot_repo(root)
按以下顺序记录：
1. `git rev-parse --verify --quiet HEAD`（optional，允许空仓库）。
2. `git symbolic-ref -q HEAD`（optional，detached HEAD 时返回非零）。
3. `git status --porcelain=v1 -z -uall --ignore-submodules=none`。
4. `git ls-files --stage -z`（索引内容）。
5. `git ls-files --cached --others --exclude-standard -z` → 跟踪 + 非忽略未跟踪路径列表。

然后对第 5 步每个路径调 `snapshot_path()`，按字节排序去重。

### snapshot_path(path)
递归记录每个路径的：
- 路径名本身。
- `os.lstat` 失败 → `missing`。
- `stat.S_IFMT(st_mode)`：文件类型。
- `stat.S_IMODE(st_mode)`：权限位。
- 符号链接 → `os.readlink` 目标。
- 普通文件 → 文件内容 SHA-256（1 MiB 块流式）。
- 目录 → 若含 `.git` 则 `snapshot_repo` 递归（子模块），否则 `sorted(os.listdir)` 递归。
- 其他类型 → `RuntimeError("unsupported file type")`。

### git(root, *args, optional=False)
- `GIT_OPTIONAL_LOCKS=0`：避免与并发 git 进程争锁。
- `CREATE_NO_WINDOW`（Windows）。
- 非零退出且非 optional → `RuntimeError(stderr)`；optional 容许退出码 1。
- 始终 `record(str(returncode))`：把退出码也纳入指纹。

### 调用点
- `claude-review.sh:82` `before_fingerprint="$(repo_fingerprint)"`。
- `claude-review.sh:183` `after_fingerprint="$(repo_fingerprint)"`；不一致 → 退出 30。
- `review_runner.py` `new_attempt()` 记 `before_fingerprint`；`worker()` finally 计 `after_fingerprint`；不一致 → `status=blocked, code=30`。

## 工作流程

```
fingerprint(repo) before CCB
  ↓
CCB 运行（只读工具，但可能触发 host 钩子/外部副作用）
  ↓
fingerprint(repo) after CCB
  ↓
before == after ? 继续校验 : 退出 30 / status=blocked
```

## 注意事项

- 指纹覆盖 **非忽略** 未跟踪内容；`.gitignore` 排除的构建产物不在指纹内。这是已知边界：CCB 若只改被忽略的产物不会被检测到。
- 子模块递归进入 `snapshot_repo`，但 `--ignore-submodules=none` 让 status 也含子模块变更。
- `snapshot_path` 对目录默认 `sorted(os.listdir)`，顺序确定。
- Windows 上 `os.lstat` 对 junction/reparse point 的处理：junction 走 `S_ISDIR` 分支并递归，可能进入 `.git` 子模块分支；普通目录则正常递归。
- `fingerprint()` 不修改任何状态，纯只读；可安全多次调用。
- `GIT_OPTIONAL_LOCKS=0` 让指纹在他人并发 git 操作时也能完成，但读到的可能是中间态；这是审计而非锁机制。

---

**相关页面**: [external-review.md](external-review.md) | [review-runner.md](review-runner.md) | [review-result.md](review-result.md) | [testing.md](testing.md) | [index.md](index.md)