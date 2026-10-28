# magent 三级路由与长任务稳健性：三阶段实施计划

版本：2026-10-28 | 原始基线：5148db65c007d3194d33bb56596ee1d33306d934（master 初始化版）  
适用：Windows / macOS / Linux；Codex 技能 magent。  
状态：阶段 1、2 由仓库维护者实施；阶段 3 **必须由使用者在本地完成真实 Codex 测试并回传证据**。

## 目标与不可改变的约束

在保留「主 Agent 维护长任务 → 有界 Worker 实现 → 新上下文只读 Verifier 验收」的前提下，增设 gpt-6.1-terra/high，降低 Luna 不适合的中等复杂任务的返工率。不是全面更换已运行稳定的 magent，不强制启动 Claude/CCB，也不因为模型更新而取消独立验收。

必须保留：主 Agent 决策权、fresh-context Worker/Verifier、文件 ownership、并发编辑保护、Verifier 缺证据即 FAIL、CCB 显式首参数 --claude-review、状态/指纹保护、安装/同步回滚能力。主模型仍由启动 Codex 的配置决定，技能不自动更换主模型。

## 阶段 1：最小风险模型路由（仓库实施）

### 变更
1. 增加角色 sol_advisor_standard，模型 gpt-6.1-terra，reasoning high；现有四个角色名称及模型不变。
2. 修改 SKILL.md 路由：solo（仅极小单文件）、routine Luna xhigh（需求确定且低风险）、standard Terra high（多模块/接口契约/中等不确定性）、high Sol high（深层根因、复杂系统约束、安全/并发/算法/破坏性迁移）。
3. Worker 可实现、运行自测，但不替代独立 Verifier。Routine verifier Luna xhigh 不变；高风险 verifier Sol high。不新增强制审查。
4. 更新 README、CLAUDE.md、.repo_wiki 的角色和路由说明；补充静态路由/角色一致性及注册检查。

### 验收标准
- 5 个角色模板齐全，标准角色在本机 agents 目录安装后，Codex config.toml 显式注册 sol_advisor_standard。
- routine / standard / high 路由可区分，且无静默 fallback。
- 外部 Claude 仅首参数 opt-in；原四角色和审核脚本不被破坏。
- 静态/维护测试通过；**真实模型调度尚待阶段 3 验收**。

## 阶段 2：可恢复长任务与有界失败升级（仓库实施）

### 变更
1. 主 Agent 仍负责长任务全局上下文，增加可选的任务检查点工具 scripts/task_state.py，数据默认位于 CODEX_HOME/magent-tasks（可用 MAGENT_TASKS_DIR 覆盖），不得保存到实际业务仓库或 Git。
2. 只对预期需要多个阶段/多个 worker 的任务建立检查点；启动、阶段完成、上下文压缩前、任务交接或故障恢复时更新。每个检查点记录目标、scope、基线、已完成、下一步、阻塞、verdict、路由、尝试次数。状态更新原子替换；任务结束显式标记完成。
3. Verifier FAIL 时优先按证据定位：同一缺陷最多一次原层定向修复；仍未通过且无新证据则 Luna→Terra→Sol；到 Sol 后仍失败则向用户报告阻塞，**不无限循环**。
4. 若失败由外部服务/凭据/环境造成，标记 blocked，等待条件变化后重试；不可把环境失败当代码成功，也不可自动启用 Claude。
5. 增加任务状态命令的离线测试以及路由/角色/文档一致性测试。旧审核器、回调生命周期不改动。

### 验收标准
- init/show/list/checkpoint/close 的合法输入与错误路径可用；数据不会覆盖其他任务；非法 ID 拒绝；私有目录权限和写入原子性有测试。
- 不存在没有上限的同缺陷修复循环；没有 PASS 就不能汇报成功。
- 主 Agent 不运行原本禁止的构建、测试、diff 验收命令；只消费 Verifier 摘要。
- 以上均可离线验证；模型决策质量仍由阶段 3 测量。

## 阶段 3：真实 Codex A/B 评测与最终调优（用户本地执行）

### 3.1 准备（必须先做）

1. 完成 git clone 或 git pull，核对阶段 1/2 新版本的 commit SHA，记为 CANDIDATE_SHA；保持此版本整个评测期间不变。
2. 旧版固定使用 BASELINE_SHA：5148db65c007d3194d33bb56596ee1d33306d934。
3. 划定测试环境：挑选**无生产凭据、无真实用户数据**的可重复仓库快照；每次任务创建独立目录/工作树。不要在真实业务 worktree 内开展 A/B。
4. 确认本机有 Python 3.9+、git、codex；确认 Codex 账号可调用 gpt-6-luna、gpt-6.1-terra、gpt-6.1-sol，支持具名 subagent。先验证 roles 注册，不要自动退回别的模型。Windows 使用 PowerShell 和原生 Python；若参与可选 CCB 实测才需要 Git Bash。
5. 两组必须使用相同的 Codex CLI 版本、主 Agent 模型与 effort、环境、测试约束、任务初始 commit、最大人工协助次数。模型版本或服务状态有差异要记录为限制，不能声称严格可归因。
6. 优先不要启用 --claude-review。第三阶段仅评估内部路由和长任务机制；外部审核作为单独可选测试。

### 3.2 复制 skill 和建立隔离的 Codex home（Linux/macOS 示例）

以下命令在一个全新测试根目录执行；不要覆盖你日常 ~/.codex 配置。

    git clone https://github.com/eoncn/magent.git magent-source
    cd magent-source
    git rev-parse HEAD   # 记录 CANDIDATE_SHA
    cd ..
    git clone https://github.com/eoncn/magent.git magent-old
    git -C magent-old checkout --detach 5148db65c007d3194d33bb56596ee1d33306d934
    mkdir -p codex-old codex-new
    python3 magent-old/tools/install.py --home "$PWD/codex-old" --roles
    python3 magent-source/tools/install.py --home "$PWD/codex-new" --roles

在两份独立的 codex-old/config.toml、codex-new/config.toml 中复制相同的认证相关**非秘密配置**和常用 Codex 设置；每套单独执行授权登录，**不要在报告中粘贴 auth.json 或令牌**。按下面的角色注册范式补齐实际的具名 subagent：

    [agents.sol_advisor_routine]
    config_file = "agents/sol-advisor-routine.toml"
    [agents.sol_advisor_high]
    config_file = "agents/sol-advisor-high.toml"
    [agents.sol_verifier_routine]
    config_file = "agents/sol-verifier-routine.toml"
    [agents.sol_verifier_high]
    config_file = "agents/sol-verifier-high.toml"

仅 codex-new/config.toml 追加：

    [agents.sol_advisor_standard]
    config_file = "agents/sol-advisor-standard.toml"

注意：安装器**只复制角色文件，不注册 config.toml**。如本机已配置 custom provider/审批准则，务必使用你已验证的配置；不要让上述片段覆盖整个 config.toml。Windows 使用：

    py -3 magent-old/tools/install.py --home "<你的绝对路径>\codex-old" --roles
    py -3 magent-source/tools/install.py --home "<你的绝对路径>\codex-new" --roles

分别手动核对每个 HOME 的 roles/config；认证储存方式依你本机实际情况设置。使用 codex exec --help 和 codex --version 验证 CLI 能力。

### 3.3 测试集：20 个任务，固定输入

从**可独立重放的 Git 提交**中选择任务，先列出 case_id / 仓库 / base_sha / 任务说明 / 验收命令 / 允许修改范围 / 难度。建议：

| ID | 数量 | 类型 | 例子 | 预期主要路由 |
| --- | ---: | --- | --- | --- |
| S01–S08 | 8 | 低风险明确开发 | 单接口参数校验、简单 CLI flag、单元测试、文档关联代码修正 | Luna |
| M01–M08 | 8 | 中等复杂度 | 多文件 API、跨模块功能、接口兼容、测试与实现协同 | Terra |
| H01–H04 | 4 | 高复杂度 | 多模块故障根因、复杂并发/权限问题、破坏性迁移的安全方案 | Sol |

最好包含 4–6 个曾出现过「低成本模型多次修复失败」的历史任务，同时确保没有预先在 prompt 里告诉模型根因。历史任务避免把修复后结果直接放在被测目录。难度标签评测前冻结，不得按结果重标。

**不可只测试旧版 Luna 与新版 Terra 的不同输入**；每个 case 必须旧版、新版各执行一次，基线代码、prompt、测试命令相同。若额度允许，高难度和失败用例各再重复一轮，以观察随机性。

### 3.4 每个 case 的确切执行操作

对于每一个 case（例如 M01）：

1. 从指定 base_sha 创建两份全新代码副本 old/M01 和 new/M01（推荐 git clone 的本地副本并 checkout --detach 同一 SHA）；不要用已经做过旧版测试的工作目录继续跑新版。
2. 复制**完全相同**的任务 prompt，明确可改范围、测试命令、禁止联网安装依赖/推生产等限制。显式调用 "$magent <任务>"（也可用 Codex 技能列表支持的等价调用，记录准确形式），不要自行指定 Terra 或 Sol。
3. 分别指定各自 CODEX_HOME，主模型和 effort 固定（例如 gpt-6.1-sol / high），执行 Codex。在实际仓库目录运行，保存 Codex JSONL、最终消息和退出码。推荐先用 1 个 S 任务冒烟验证命令行、路由注册和工具许可，再批量跑。
4. Linux/macOS 命令示例（在每组已配置的环境中，$PROMPT_FILE 是实际 prompt 文件路径，$TASK_DIR 是测试仓库路径）：

       CODEX_HOME="<绝对路径>/codex-old" codex exec -C "$TASK_DIR" -m gpt-6.1-sol -c 'model_reasoning_effort="high"' --sandbox workspace-write --json - < "$PROMPT_FILE" > "old-M01.jsonl" 2> "old-M01.stderr.log"
       CODEX_HOME="<绝对路径>/codex-new" codex exec -C "$TASK_DIR" -m gpt-6.1-sol -c 'model_reasoning_effort="high"' --sandbox workspace-write --json - < "$PROMPT_FILE" > "new-M01.jsonl" 2> "new-M01.stderr.log"

   以上两个 TASK_DIR 必须是**不同的干净仓库副本**。prompt 文件第一行必须显式激活 magent，示例：

       $magent 修复指定任务；仅修改 <scope>；运行 <commands>；最后返回 VERDICT 和路由摘要。

   如果 CLI 无法触发技能或无法产生 subagent，记录为 setup_fail，不计入模型质量对比，先修注册/版本差异。PowerShell 里分别设置 $env:CODEX_HOME，执行 codex exec -C ... --json -- ...（具体 stdin 重定向按 PowerShell 版本采用 Get-Content -Raw | codex exec ... -）。
5. 在模型结束后，由**测试操作者或独立脚本**在对应副本中执行固定验收命令，保存 exit code；不要仅依据 Agent 自称的 PASS。
6. 从 Codex JSONL 记录 wall 时间、用量（若提供：input_tokens、cached_input_tokens、output_tokens）、真实路由（routine/standard/high）、Worker/Verifier 次数、失败重试次数、是否 checkpoint/recover。Codex JSONL 的事件字段可能随版本变化；没有字段填空，不推测 token。
7. 如果 timeout/限额/网络错误，标为 infra_fail，**不要混入模型能力失败**；失败现场保留但报告中移除隐私/令牌。
8. 执行顺序采用 S01 旧→新，S02 新→旧，交错顺序减少时间段/服务负载偏差；每次执行都用独立副本。

### 3.5 结果 CSV 与自动汇总

将全部配对结果填写到 stage3-results.csv（UTF-8）。每个 case 两行，建议字段固定为：

    case_id,variant,difficulty,project,base_sha,route,verdict,acceptance_pass,elapsed_sec,input_tokens,output_tokens,worker_calls,verifier_calls,retries,checkpoint_recovered,notes

取值规范：
- variant：old / new
- difficulty：S / M / H
- verdict：pass / fail / blocked / infra_fail / setup_fail
- acceptance_pass：1 / 0；实际验收没有执行完必须填 0，verdict 不能写 pass
- elapsed_sec：墙钟秒数，整数或小数；未知时留空
- token 与调用数字：未知时留空，不能填臆测的 0
- checkpoint_recovered：1 / 0 / 空白；不涉及恢复填 0
- notes：简短错误分类，禁止秘密信息

运行：

    python3 magent-source/tools/analyze_stage3.py stage3-results.csv --out stage3-report.md

分析工具会校验配对完整性，计算全量与 S/M/H 分层的成功率、总时长、已知 token 合计、重试次数、路由分布；缺失/基础设施失败样本必须单独列示，不能混进成功率分母。

### 3.6 决策门槛与回退方案

预先采用这些保守阈值，**不把小样本变化当统计学证据**：

- 质量：新版通过数 **不得低于** 旧版；每个新增标准路由的案例必须有可复现的验收命令。H 类型不得引入新回归。
- 效率：若质量不退化，目标是失败重试次数下降，或有效任务平均耗时下降；token 数据只在完整记录的配对样本间比较。
- 稳定：两个 CODEX_HOME 的角色注册成功，无越界文件变更；主 Agent 能在断点后继续既有任务，外部审核默认关闭。
- 阻断发布：缺陷导致失败无限循环、状态文件破坏、scope 违规、把未通过验收当作 PASS、自动开启外部审核。
- 当结果不达标：仅对导致失败的路由规则回退，保留经过验证的任务状态工具；避免整个架构回滚。保存 candidate commit SHA 便于恢复。

### 3.7 必须附加的长任务恢复专项测试

选择一个真实跨阶段任务（建议 3 个里程碑）：
1. 发起任务，使主 Agent 运行 task_state.py init 保存 objective/base_sha/scope 和当前里程碑（获取 task_id、revision=0）。
2. 完成第 1 里程碑并运行 checkpoint --expected-revision 0；核对 show 的 revision=1、completed 阶段记录，以及未通过验收时不能执行 close（completed 列表记录阶段进展，不等于最终 PASS）。
3. 在会话压缩、Codex exec resume 或新启动的会话中，给出 task_id（不复制此前全部上下文），要求先 show 再继续；记录恢复的下一步、是否遗漏文件 ownership。
4. 模拟一次 Verifier FAIL，核对同缺陷只在原层修复一次；再次失败应逐层升级且有停止界限。**不得为测试故意改动生产仓库**。
5. 所有阶段的独立验收通过后，先 checkpoint --expected-revision <n> --phase verification --verdict pass --next "" --blocker ""，获取新 revision，再 close --expected-revision <new-n>；检查 status=closed。

这项恢复测试至少旧版、新版各执行一次；旧版没有 task_state 工具时记录 “N/A，依赖会话上下文”，不能伪造对应数字。

### 3.8 本地交付给 ChatGPT 的材料（用于最后优化）

请回传：
- stage3-results.csv 与 stage3-report.md
- case-manifest.md：20 项 case 的 base_sha、prompt、验收命令、难度（敏感路径可脱敏）
- 失败案例的精简日志（首个根因、路由、exit code、Verifier 摘要、相关 diff），**不要发送完整凭据或 auth.json**
- codex --version、主模型/effort、操作系统、旧版 SHA、新版 SHA
- 长任务专项测试报告：阶段断点、恢复正确性、失败升级行为与实测问题

收到这些证据后才能做阶段 3 的最终调优：更改阈值、必要时决定 Luna xhigh→high、是否增加 Terra verifier、是否简化验证门。此时才评价成本/成功率改进，不应提前承诺提升百分比。

## 阶段 1/2 仓库交付验收命令

    python3 tools/test_maintenance.py
    python3 scripts/test_review_runner.py
    python3 scripts/test_sync_review.py "$PWD/scripts/claude-review.sh"   # POSIX
    python3 scripts/test_task_state.py
    python3 tools/test_skill_config.py
    python3 tools/check_role_registration.py --home ~/.codex

最后一条只读验证现有 Codex home，缺少角色时应报告并退出非 0，不修改 config.toml。第一至四条是离线回归，不代表真实 Codex 模型运行已通过。
