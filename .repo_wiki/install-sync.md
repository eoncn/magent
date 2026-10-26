# 安装与同步（tools/）

> `install.py` 把本检出链接进 Codex home；`sync.py` 通过 SSH 把已配置远端检出快进到本地 HEAD。

## 概述

`magent` 是技能目录，安装就是让 `~/.codex/skills/magent` 指向本仓库。POSIX 用 symlink，Windows 用 junction（CPython `_winapi.CreateJunction`，无需开发者模式或符号链接权限）。`install.py` 默认链接 `~/.codex` 和 `~/.codex-us` 两个 Codex home，可选 `--roles` 同时复制四个角色模板到 `<home>/agents/`。旧入口备份到 `<home>/magent-install-backups/<stamp>/...`，失败自动回滚。

`sync.py` 不克隆、不提交、不推送。它通过免密 SSH 在每个远端主机上执行一段内嵌 Python：验证远端是仓库根、工作树干净、有 upstream、upstream HEAD 等于本地 HEAD，然后 `git fetch + merge --ff-only`。`--install` 在远端再跑一次 `install.py` 把检出链接进远端的 Codex home。

## 关键文件

| 文件路径 | 说明 |
|---------|------|
| `tools/install.py` | 链接本检出到 Codex home，含备份/回滚 |
| `tools/sync.py` | SSH 快进远端检出，可选 `--install` |
| `tools/test_maintenance.py` | 安装/回滚/同步/共享检出状态回归测试 |
| `roles/*.toml` | 四个角色模板（`--roles` 时复制） |

## 核心概念

### install.py

#### 命令
```bash
python3 tools/install.py --dry-run       # 预览
python3 tools/install.py                 # 链接 ~/.codex 与 ~/.codex-us
python3 tools/install.py --home /path    # 自定义 home，可重复
python3 tools/install.py --roles         # 同时复制角色模板
```

#### 安装步骤
1. `homes` 去重 + `expanduser().absolute()`。
2. 对每个 home：
   - `target = home / "skills/magent"`。
   - 若 `SOURCE.is_relative_to(target.resolve())`：源已在目标内。若已正确链接 → 跳过；否则报错（拒绝把源装进自己）。
   - 否则加入 entries。
   - `--roles`：对 `roles/*.toml` 每个文件，若 `<home>/agents/<name>` 内容相同 → 跳过；否则加入 entries。
3. `--dry-run`：返回 changes 列表，不创建任何目录。
4. 执行：对每个 entry：
   - `target.parent.mkdir(parents=True, exist_ok=True)`。
   - `temporary = target.with_name("." + target.name + "." + uuid + ".tmp")`。
   - directory → `link_directory(source, temporary)`；file → `shutil.copyfile`。
   - 若 `os.path.lexists(target)`：备份到 `<home>/magent-install-backups/<stamp>/<relative>`，`os.replace(target, backup)`。
   - `os.replace(temporary, target)`。
5. 失败：清理 pending temporary；对已完成的反向恢复（删新链接、`os.replace(backup, target)`）。

#### link_directory
- POSIX：`link.symlink_to(target, target_is_directory=True)`。
- Windows：`_winapi.CreateJunction(str(target), str(link))`。

#### linked_to
- `path.is_symlink() or path.is_junction()` 且 `path.resolve() == target.resolve()`。

#### remove_entry
- junction → `rmdir`；symlink/file → `unlink`；其他 → `shutil.rmtree`。

#### 备份与回滚
- `stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]`。
- 备份位置：`<home>/magent-install-backups/<stamp>/<target.relative_to(home)>`。
- 同一次安装出错回滚已替换入口；重复安装不会重复备份正确链接。
- `--roles` 复制的角色文件若字节相同则跳过，避免无意义备份。

### sync.py

#### 命令
```bash
python3 tools/sync.py --host macos-server --host home-server --dry-run
python3 tools/sync.py --host macos-server --home-server --install
python3 tools/sync.py --path ~/otherWorks/magent --host ...
```

#### 本地预检
1. `git -C SOURCE rev-parse --show-toplevel` 必须等于 `SOURCE`（要求本目录是 git 仓库根）。
2. `git status --porcelain=v1 --untracked-files=all` 必须为空（提交本地修改后再同步）。
3. `commit = git rev-parse HEAD`。

#### 远端程序（内嵌 Python，通过 stdin 传）
- 用户数据通过 stdin 传，**绝不** 插值进 SSH 命令。
- 远端：
  1. `git rev-parse --show-toplevel` 必须等于目标路径。
  2. `git status --porcelain=v1 --untracked-files=all` 必须为空。
  3. `upstream = git rev-parse --abbrev-ref --symbolic-full-name @{upstream}`。
  4. `remote = git config branch.<current>.remote`。
  5. `--dry-run`：打印 `{checkout, current, expected, upstream, install, dry_run}`。
  6. 否则：`git fetch -- <remote>`。
  7. `git rev-parse <upstream>` 必须等于本地 `commit`（upstream 已含本次推送）。
  8. `git merge-base --is-ancestor HEAD <commit>`（本地 HEAD 是远端 HEAD 的祖先，可快进）。
  9. `git merge --ff-only <commit>`。
  10. `--install`：`python3 tools/install.py`。
  11. 打印 `{checkout, commit}`。

#### 主机迭代
- `dict.fromkeys(args.host)` 去重保序。
- `host.startswith("-")` 拒绝（避免被当成 SSH 选项）。
- `ssh -o BatchMode=yes -o ConnectTimeout=10 host python3 -`，stdin 喂远端程序。
- 逐主机执行；某台失败 → 停止；已成功主机保留更新，重跑可继续。

## 工作流程

```
本仓库改源码 → 提交 → 推送
  ↓
python3 tools/sync.py --host A --host B --dry-run    # 预检
  ↓
python3 tools/sync.py --host A --host B --install    # 快进 + 链接
  ↓
远端 A/B: git fetch → ff-only → install.py 链接 Codex home
```

## 配置

| 参数 | 说明 |
|------|------|
| `install.py --home` | Codex home，可重复，默认 `~/.codex` + `~/.codex-us` |
| `install.py --roles` | 同时复制角色模板 |
| `install.py --dry-run` | 预览 |
| `sync.py --host` | SSH alias，可重复 |
| `sync.py --path` | 远端检出路径，默认 `~/otherWorks/magent` |
| `sync.py --install` | 远端同步后再跑 install.py |
| `sync.py --dry-run` | 双方只读检查 |

## 注意事项

- `install.py` 不修改 `config.toml`；角色注册依赖目标机器现有的 agent 配置机制。
- 安装前确认 `sol_advisor_routine/sol_advisor_high/sol_verifier_routine/sol_verifier_high` 都能被当前 Codex 环境调用。
- `sync.py` 要求本地与远端工作树都干净；远端有未提交修改 → 停止。
- `sync.py` 不自动克隆、提交、推送或覆盖本地修改；只快进到本地 HEAD 对应的同一提交。
- `sync.py` 要求 upstream 已包含本地 HEAD：先 `git push` 再 sync。
- 回退版本也在 Git 操作；入口仍链接到同一检出。回退前先结束现有外部审核任务，避免后台进程跨版本加载脚本。
- 原安装备份 `<home>/magent-install-backups/<stamp>/` 可用于手工恢复入口。
- 共享检出时建议各启动入口明确设置 `CODEX_HOME`，使两套 Codex 各自保留运行状态。

---

**相关页面**: [cross-platform.md](cross-platform.md) | [testing.md](testing.md) | [overview.md](overview.md) | [index.md](index.md)