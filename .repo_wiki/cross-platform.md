# 跨平台执行（Windows / POSIX）

> 同一源码在 Linux、macOS、Windows 上运行；Windows 用 Job Object + 字节范围锁替代 POSIX 信号/进程组/flock。

## 概述

`magent` 在三平台共享同一分支。`.gitattributes` 强制 `*.sh/*.py/*.md/*.toml` 用 LF 行尾，避免 Windows CRLF 漂移。运行时差异集中在进程管理、文件锁、shell 启动：Windows 用 `review_windows.py` 的 Job Object + `msvcrt.locking` + `OpenProcess/NtQueryInformationProcess`；POSIX 用 `fcntl.flock` + `os.killpg` + `ps`。

Windows 必须使用 **Git Bash**，不能用 System32 Bash/WSL 启动器。Python 原生运行（CPython 3.12+）。npm `.cmd` 入口被解析到相邻的 Git Bash 启动器，避免回调 payload 经过 `cmd.exe` 被命令行解析吃掉。

## 关键文件

| 文件路径 | 说明 |
|---------|------|
| `.gitattributes` | 强制 LF 行尾 |
| `scripts/review_windows.py` | Windows 进程/作业 API |
| `scripts/review_runner.py` | 平台分支（`os.name == "nt"`） |
| `scripts/review_support.py` | `CREATE_NO_WINDOW` 标志 |
| `tools/install.py` | Windows junction 安装 |
| `references/external-review.md` | Windows 执行章节 |

## 核心概念

### 进程身份
- **POSIX** `identity(pid)`：`ps -ww -p <pid> -o stat= -o lstart= -o command=`，取 `lstart+command`；跳过僵尸（`Z` 开头）。
- **Windows** `identity(pid)`：`OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` → `GetExitCodeProcess`（259=STILL_ALIVE）→ `GetProcessTimes`（创建时间）→ `NtQueryInformationProcess(60)`（命令行）。返回 `str(created) + " " + command`。
- `owned(record, prefix)` 比对 `prefix_pid` + `prefix_identity` + `nonce ∈ command`；不匹配拒绝信号，避免 PID 复用误杀。

### 进程组 / Job Object
- **POSIX**：`start_new_session=True` 让 worker 成为会话 leader；`terminate_group(pgid)` 用 `os.killpg(SIGTERM)` → 等 2s → `SIGKILL`。`group_alive(pgid)` 用 `ps -axo pgid=,stat=`。
- **Windows**：`review_windows.spawn()` 创建命名 Job Object `Local\magent-review-<nonce>`，设 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`，用 `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | 4`（CREATE_SUSPENDED）启动进程，`AssignProcessToJobObject` 后 `NtResumeProcess`。监督器异常退出时 Job 关闭杀掉所有后代。
- `terminate_group(pgid, nonce)`：Windows 上 `OpenJobObjectW` → `TerminateJobObject(130)` → 等 5s `active==0`。

### 文件锁
- **POSIX** `lock()`：`fcntl.flock(fd, LOCK_EX)`。
- **Windows** `lock()`：`msvcrt.locking(fd, LK_NBLCK, 1)` 轮询，0.05s 退避；文件初始大小 0 时先写 `b"0"`。
- `write_json` Windows 上 `os.replace` 可能 `PermissionError`（ antivirus/索引扫描占用），重试 2 秒。

### Shell 启动
- `shell_env()` 清空 `BASH_ENV`/`ENV`：避免 SSH 初始化注入背景进程。
- `tool_args(value)`：Windows 上非 `.exe` 启动器用 `[bash, --noprofile, --norc, shell_launcher(value)]`；POSIX 直接 `[value]`。
- `shell_launcher(value)`：Windows 上 `.cmd`/`.bat` 改成无后缀同名脚本（Git Bash launcher）；缺失则失败。
- `bash_path(value)`：Windows 把反斜杠转正斜杠，方便 bash 命令行。

### Codex queue 投递
- **POSIX** `send_queue()`：`[launcher, "queue", "--thread", thread, "--message", message]`。
- **Windows**：MSYS 命令行会吃掉 JSON 反斜杠。通过环境变量 `MAGENT_QUEUE_LAUNCHER/THREAD/MESSAGE` 传 payload，`MSYS2_ARG_CONV_EXCL="*"` 禁用参数转换，bash 内 `exec "$MAGENT_QUEUE_LAUNCHER" queue --thread "$MAGENT_QUEUE_THREAD" --message "$MAGENT_QUEUE_MESSAGE"`。
- `timeout=10`，`capture_output=True`，`CREATE_NO_WINDOW`。

### 安装：junction vs symlink
- `tools/install.py` `link_directory()`：Windows 用 `_winapi.CreateJunction`（无需符号链接权限/开发者模式）；POSIX 用 `Path.symlink_to(target, target_is_directory=True)`。
- `linked_to()`：同时检查 `is_symlink()` 和 `is_junction()`（CPython 3.12+ 的 `Path.is_junction()`）。
- `remove_entry()`：junction 用 `rmdir`，symlink/file 用 `unlink`，其他用 `shutil.rmtree`。

### 仓库指纹
- `review_support.py` 在 Windows 上用 `CREATE_NO_WINDOW` 启动 git 子进程。
- `os.lstat` 对 junction/reparse point：junction 走 `S_ISDIR` 分支并递归。
- Windows ACL 不保证 POSIX mode 位；`0700` 目录 / `0600` 文件在 Windows 继承用户 profile ACL，不是 ACL 安全边界。

### 测试覆盖
- `test_review_runner.py` 在 Windows 上 `from review_windows import identity as windows_identity, group_alive as windows_group_alive`，有专门的 Job Object 崩溃覆盖。
- `test_sync_review.py` 用 POSIX-only 文件名/FIFO/mode/symlink 夹具，Windows 跳过该套件。
- `test_maintenance.py` 跨平台。

## 工作流程

```
review_runner.py main()
  ↓
if os.name == "nt":
    from review_windows import identity, group_alive, terminate_group, terminate, spawn as windows_spawn
  ↓
worker() / control() 内分支:
  POSIX: start_new_session=True / os.killpg / fcntl.flock
  Windows: review_windows.spawn / Job Object / msvcrt.locking
```

## 注意事项

- 不要用 System32 Bash/WSL 启动 `claude-review.sh`；只用 Git Bash。
- Windows 上 `os.replace` 重试仅针对 `PermissionError`，其他错误立即抛。
- Job Object 名字 `Local\magent-review-<nonce>`：`Local\` 前缀让名字在会话内可见，不跨会话。
- `CREATE_SUSPENDED`（flag 4）+ `NtResumeProcess`：确保子进程在被 assign 到 job 之前不会 spawn 孙进程逃出 job。
- `NtQueryInformationProcess(60)` 是未文档化信息类 60（ProcessBasicInformation 之外的命令行类）；status < 0 表示失败。
- Windows 上 `executable()` 对绝对路径且存在但 `shutil.which` 找不到的文件保留原值，让 Git Bash 能执行无后缀 shell launcher。
- `MSYS2_ARG_CONV_EXCL="*"` 是 MSYS2 的全局参数转换禁用开关；只在我们控制的 bash 调用里用。

---

**相关页面**: [review-runner.md](review-runner.md) | [review-support.md](review-support.md) | [install-sync.md](install-sync.md) | [testing.md](testing.md) | [overview.md](overview.md)