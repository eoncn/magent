# 离线测试体系

> 六套测试，全部离线运行：mocked CCB/queue，不发送模型请求或真实回调。

## 概述

`magent` 的测试目标是验证安装/同步/审核生命周期的逻辑正确性，不依赖真实 CCB、Codex 凭据或授权线程。CCB 与 `codex queue` 用本地 fixture 替代；Git 用真实 git。三套测试覆盖三个层次：维护工具（install/sync）、监督器生命周期（review_runner）、同步审核 envelope（sync_review）。

第三套 `test_sync_review.py` 用 POSIX-only 文件名/FIFO/mode/symlink 夹具，只能在 Linux/macOS 跑。前两套跨平台。

## 关键文件

| 文件路径 | 说明 |
|---------|------|
| `tools/test_maintenance.py` | install/sync/共享检出状态回归 |
| `scripts/test_review_runner.py` | 监督器生命周期 E2E |
| `scripts/test_sync_review.py` | 同步审核 envelope/schema parity |
| `scripts/claude-review.sh` | 被测入口 |
| `scripts/review_runner.py` | 被测监督器 |

## 核心概念

### test_maintenance.py
- `InstallationTests`：dry-run 不建目录；备份共享链接 + 幂等；第二 home 失败恢复两个原入口；`--roles` 复制；junction/symlink 检测。
- 同步测试：本地脏工作树拒绝；远端脏拒绝；upstream 不一致拒绝；分支分叉拒绝；`--dry-run` 只读；`--install` 远端链接。
- 共享检出状态：`CODEX_HOME` 未设时默认 `~/.codex/magent-runs`，不写入仓库。
- 用 `tempfile.TemporaryDirectory` + `unittest.mock.patch` 隔离文件系统。

### test_review_runner.py
- `ENTRY = claude-review.sh`，`BASH = shutil.which("bash")`。
- `CCB` fixture：内嵌 Python 脚本，读 stdin packet，按 `FIXTURE_MODE` 模拟 success/mutation/mutation_timeout/ignore_term/slow 等模式；写 `FIXTURE_ARGS` 记录调用参数；检查 `BASH_ENV/ENV` 不存在、`CODEX_THREAD_ID` 不继承。
- 覆盖：同步 PASS、后台 launch + callback、超时、mutation 检测（指纹变化 → 退出 30）、stop/resume、retry-callback、PID 复用拒绝、orphaned group、overdue 通知、queue 失败重试 3 次。
- Windows 专门覆盖 Job Object 崩溃（`review_windows` 导入）。

### test_sync_review.py
- `REAL_GIT = shutil.which("git")`，`BASH = shutil.which("bash")`。
- `FAKE_CCB`：内嵌 Python，断言 CCB 调用参数：`--permission-mode dontAsk`、`--model audit-model`、`--effort high`、`--output-format json`、`--no-session-persistence` 不存在、`--session-id` 或 `--resume` 存在、`--progress/--disable-slash-commands/--no-chrome/--strict-mcp-config` 存在、`--setting-sources user`、`--tools Read,Glob,Grep`、`--mcp-config {"mcpServers":{}}`、sandbox `failIfUnavailable=True`、`--json-schema additionalProperties=False`。
- POSIX-only 夹具：`WEIRD_NAME`（含换行/非 ASCII 字节的文件名）、FIFO、mode 位、symlink。Windows 跳过。
- 验证 envelope：CCB 事件数组、缺 `structured_output`、schema 漂移、verdict/finding 矛盾、`NaN/Infinity` 拒绝。

### 新增阶段 1/2 测试

- `tools/test_skill_config.py`：静态路由、5 角色映射、配置注册检测，不请求真实模型。
- `scripts/test_task_state.py`：任务状态创建/修订/冲突/验收关闭/权限/恢复路径，不访问业务仓库。
- `tools/analyze_stage3.py` 与 `tools/test_stage3_analyzer.py`：本地配对 CSV 严格校验及汇总，不替代真实 Codex A/B。

## 测试入口
```bash
python3 tools/test_maintenance.py
python3 tools/test_auto_install.py
python3 tools/test_skill_config.py
python3 scripts/test_task_state.py
python3 tools/test_stage3_analyzer.py
python3 scripts/test_review_runner.py
python3 scripts/test_sync_review.py "$PWD/scripts/claude-review.sh"   # POSIX only
```
全部从仓库根执行。前两条跨平台；第三条 Linux/macOS。

## 工作流程

```
test_maintenance: tempfile → install/sync → 断言链接/备份/回滚/远端 ff-only
  ↓
test_review_runner: fixture CCB + 真实 git → 跑 claude-review.sh → 断言状态/回调/指纹/超时
  ↓
test_sync_review: FAKE_CCB + POSIX 夹具 → 跑同步审核 → 断言 envelope/schema/verdict
```

## 注意事项

- 测试 **不** 暗示 live integration 通过：真实 CCB 调用与真实 queue 投递需要授权 Codex 线程 + CCB 认证，是额外检查。
- `test_review_runner.py` 的 fixture CCB 通过环境变量 `FIXTURE_MODE/FIXTURE_ARGS` 控制行为；真实 CCB 不认这些变量。
- `test_sync_review.py` 第一个参数是 `claude-review.sh` 路径：`"$PWD/scripts/claude-review.sh"`。
- Windows 测试跳过 POSIX-only 夹具，但跑 `test_review_runner.py` 的 Job Object 崩溃用例。
- 测试用真实 git 操作临时仓库；不会触碰本仓库工作树。
- 改 `review_runner.py` / `review_support.py` / `review_result.py` / `claude-review.sh` 后应跑全部三套。
- 改 `install.py` / `sync.py` 后至少跑 `test_maintenance.py`。

---

**相关页面**: [review-runner.md](review-runner.md) | [review-support.md](review-support.md) | [review-result.md](review-result.md) | [install-sync.md](install-sync.md) | [cross-platform.md](cross-platform.md) | [index.md](index.md)
## 2026-10 Terra standard 定向试验

- `docs/terra-standard-targeted-trial.md`：筛选 6～8 个任务，路由预筛选后 4 对 Luna 与 Terra 的隔离比较；不改变生产路由、正式模型或独立 verifier。
- `tools/prepare_terra_trial.py`：复制两份相同 magent、注册角色，只改变实际安装 standard Worker 的模型和 effort，不复制认证、不触发模型调用。
- `tools/analyze_terra_trial.py`：检查成对基线、prompt hash、自然 standard 路由、实际 standard Worker 调用、对应模型/verifier pin，排除环境与配置失败，统计质量/时间/Token。
- `python3 tools/test_terra_trial.py`：仅离线检验以上工具，不代表真实 Codex 功能测试已通过。
- 实验结果回交后才能评估是否微调标准路由；本轮不修改 CCB / `SKILL.md` / Verifier 参数。

## 自动注册安装验证（2026-10）

`tools/test_auto_install.py` 是离线单元测试，不调用 Codex 或真实模型；覆盖自动注册五个角色、原有 provider/审批和注释保留、BOM/CRLF、再次执行幂等、无效 TOML 或同名冲突预防、显式禁用拒绝、配置软链接保护、多 Home 预检查以及中间失败或安装后校验失败时的回滚。注册模块使用 Python 3.11+ `tomllib` 或可选 `tomli`。上线操作仍需实际使用 `tools/check_role_registration.py` 校验并确认具名模型能派发。
