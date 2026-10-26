# review_runner.py 监督器

> 外部审核的 Python 生命周期监督器：create/launch/worker/control 四阶段。

## 概述

`scripts/review_runner.py` 是外部审核的后端。Bash 入口 `claude-review.sh` 在检测到生命周期子命令或 `--detach` 时 `exec` 到这个 Python 脚本。它管理运行目录、持久 CCB 会话、进程身份、超时、Codex 队列回调与 stop/resume/retry-callback/cleanup。同步模式仍由 Bash 入口自己处理 CCB 与校验；监督器只接管后台与控制路径。

模块在 Windows 上额外导入 `review_windows.py` 的 `identity/group_alive/terminate_group/terminate/spawn`，用 Job Object 与字节范围锁替代 POSIX `flock`/进程组/信号。

## 关键文件

| 文件路径 | 说明 |
|---------|------|
| `scripts/review_runner.py` | 监督器主体 |
| `scripts/claude-review.sh` | 入口转发（`exec python3 review_runner.py "$@"`） |
| `scripts/review_support.py` | `fingerprint()`，前后指纹比对 |
| `scripts/review_windows.py` | Windows 进程/作业 API |
| `references/external-review.md` | 状态/回调协议规范 |

## 核心概念

### 运行根目录
`runs_root()`：`MAGENT_REVIEW_RUNS_DIR` 或 `${CODEX_HOME}/magent-runs`。共享检出（`SCRIPT.parents[2].name == "skills"`）默认用 `~/.codex/magent-runs`；直接 `skills/magent` 安装默认用 `$CODEX_HOME/magent-runs`。`create()` 拒绝把运行目录设在仓库内或仓库的父级是仓库。

### 状态与锁
- `state.json`：`version=1`、`run_id`、`run_dir`、`mode`、`repo_root`、`thread_id`、`attempts[]`、`current_attempt`。
- 每个 attempt：`attempt, nonce, status, session_id, resumed_from_attempt, eta_seconds, timeout_seconds, config, before_fingerprint, callbacks, progress_log, result_path`，运行中追加 `worker_pid/worker_identity/cli_pid/cli_identity/started_at/overdue_at/after_fingerprint/completed_at/exit_code/event/error`。
- `write_json`：`O_WRONLY|O_CREAT|O_EXCL` + `os.replace` 原子替换，`fsync`；Windows 上 `PermissionError` 重试 2 秒。
- `safe_open`：`O_NOFOLLOW`，拒绝符号链接状态路径。
- `lock()`：POSIX `fcntl.flock(LOCK_EX)`；Windows `msvcrt.locking(LK_NBLCK)` 轮询。
- `load_state` 校验 `version/run_id/run_dir/mode/attempts/current_attempt` 与每个 attempt 的 `attempt` 序号和 `nonce` UUID 形式。

### 进程身份
- `identity(pid)`：POSIX `ps -ww -p <pid> -o stat= -o lstart= -o command=`，取 `lstart+command` 元组；跳过僵尸（`Z`）。
- `owned(record, prefix)`：比对 `prefix_pid` + `prefix_identity` + `nonce` ∈ command；不匹配拒绝信号。
- Windows 用 `OpenProcess` + `GetProcessTimes` + `NtQueryInformationProcess(60)` 取创建时间与命令行。

### create(args)
1. 解析 `thread_id`（`--thread-id` 或 `CODEX_THREAD_ID`，detach 必填）与 `eta_seconds`（detach 必填）。
2. `preflight(thread)`：解析 `ccb/bash/model/effort`，验证 `git/python3`，若 `thread` 非空则验证 `codex queue --help` 含 `--thread`/`--message`。
3. `repo_root(args.repo)`：`git rev-parse --show-toplevel`。
4. 读 stdin packet，拒绝空 packet。
5. 创建 `<runs_root>/<uuid>/`（`0700`），写初始 `state.json`。
6. `new_attempt()`：建 `attempt-N/`（`0700`）与 `packet/progress/output/supervisor`（`0600`），记 `before_fingerprint`。
7. `launch()`。

### launch(run, record, detach)
- spawn `python3 -B review_runner.py __worker --run-dir ... --attempt ... --nonce ...`。
- POSIX `start_new_session=True`；Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`。
- detach：打印 launch JSON（`run_id, run_dir, attempt, session_id, status=running, eta_seconds, progress_log, worker_pid`）后返回。
- 同步：`proc.wait()`，`KeyboardInterrupt` 时 POSIX `SIGTERM`、Windows 改状态为 `stopping` 让 worker 自检；最终读 `result.json` 或抛 `ReviewError`。

### worker(run, number, nonce)
1. 重验身份与 attempt 状态；装 `SIGTERM/SIGINT` → `stopping` Event。
2. 清空 `CODEX_THREAD_ID/CODEX_SESSION_ID/.../BASH_ENV/ENV`，注入 `CLAUDE_CCB_BIN/CLAUDE_REVIEW_MODEL/CLAUDE_REVIEW_EFFORT/MAGENT_REVIEW_SESSION_ID/MAGENT_REVIEW_RESUME`。
3. 再取指纹比对 `before_fingerprint`；不一致 → 退出码 30。
4. 构造命令：`bash --noprofile --norc -c '"$3" --noprofile --norc "$1" --claude-review "$2"' magent-review-<nonce> <SCRIPT> <mode> <bash>`。nonce 嵌入命令行以便 stop 时身份校验。
5. spawn CCB；轮询 `proc.poll()`，每 0.05s 检查 stopping / timeout / overdue。
   - overdue：首次到 ETA 时后台线程投 `overdue` 回调。
   - timeout：硬超时 `terminate(proc)`，记 `error="Review exceeded hard timeout"`。
6. 退出码 0 且 output ≤ 16 MiB → 读 `stdout.json`，验证 `verdict` ∈ `{ship,fix-first,rethink}` → `status=completed`。
7. `finally`：`terminate(proc)`；再取 `after_fingerprint` 比对；不一致 → `status=blocked, code=30`。
8. 写 `result.json`（仅 completed）；更新 attempt 终态；`queue_callback(run, number, status)`。

### control(args)
- `inspect`：打印 `state.json`。
- `retry-callback`：对终态 attempt 重投 `record["event"]`。
- `stop`：`owned()` 验证 worker/cli PID 后信号；若 worker 未注册身份则仅改状态让 worker 自检；orphaned group 拒绝不安全恢复。
- `resume`：仅 stopped/blocked 且无活进程；`new_attempt(..., resume=record)` append 新 attempt，传 `--resume <session_id>`。
- `cleanup`：仅 completed `ship` 且回调已解决且无活进程；`shutil.rmtree(run)`。

### queue_callback(run, number, event)
- `callback.lock` 下：若已 `sent` 或 overdue 对非 running → 跳过。
- `sending` → `send_queue()` → `sent`（记 tries）。
- 失败重试 3 次，退避 `0.5 * 2^attempt`；最终 `failed` 记 `error`。
- `event_message`：`$magent internal callback\n` + JSON `{run_id, run_dir, mode, attempt, event, delivery_id}`。
- `delivery_id = f'{run_id}:{attempt}:{event}'`：稳定 ID 供消费方去重。

### send_queue(launcher, thread, message, cwd)
- POSIX：`[launcher, "queue", "--thread", thread, "--message", message]`。
- Windows：MSYS 命令行会吃掉 JSON 反斜杠，所以通过环境变量 `MAGENT_QUEUE_LAUNCHER/THREAD/MESSAGE` 传 payload，`MSYS2_ARG_CONV_EXCL="*"`，bash 内 `exec` 展开。
- `shell_env()` 清空 `BASH_ENV/ENV`，避免 SSH 初始化注入后台进程。
- `timeout=10`，`capture_output=True`，`CREATE_NO_WINDOW`（Windows）。

## 配置

| 参数 | 说明 |
|------|------|
| `action` | `final|commitment|inspect|resume|stop|retry-callback|cleanup|__worker` |
| `--detach` | 后台运行 |
| `--thread-id` | 回调目标 Codex 线程 |
| `--eta-seconds` | 预期完成时间，触发 overdue |
| `--timeout-seconds` | 硬超时，默认 2×ETA |
| `--repo` | 仓库路径，默认 `os.getcwd()` |
| `--run-dir` | 控制子命令必需 |
| `--attempt` / `--nonce` | `__worker` 内部用，`SUPPRESS` 帮助 |

## 注意事项

- `os.umask(0o077)` 在 `main()` 起始设置，确保所有新建文件默认 `0600`。
- `__worker` 是内部子命令，不应直接调用。
- 监督器只实现 review stage，**不** 派发实现工作。
- `review_runner.py` 通过 `sys.dont_write_bytecode = True` 避免写 `.pyc`。
- Windows 上 `import review_windows` 仅在 `os.name == "nt"` 时执行，避免 POSIX 缺少 ctypes 字段。

---

**相关页面**: [external-review.md](external-review.md) | [review-support.md](review-support.md) | [review-result.md](review-result.md) | [cross-platform.md](cross-platform.md) | [testing.md](testing.md) | [index.md](index.md)