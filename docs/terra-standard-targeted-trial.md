# Terra 标准 Worker 专项 A/B：4 对、8 次正式运行

版本：2026-10-28；实验类型：**隔离测试，非生产路由更改**。  
稳定参考：magent master `4be9e508fc8a4237f6838e498d6079d8f2c0aab8`（实验准备前记录并锁定实际 commit）。  
上次实测：[第三阶段复盘](stage3-evaluation-2026-10-28.md)：20 对任务全部通过，但 M 类 8/8 初始路由为 routine、正式任务没有实际使用 Terra standard；性能没有可归因收益。

## 实验目的、严守的边界

只回答：**在天然适合 standard Worker 的中等复杂度任务上，固定 magent 工作流后，Terra/high 与 Luna/xhigh 的实现质量、返工、时间、可观测 Token 有何差异？**

不能用此次结果宣称新 magent 相比旧 magent 的整体效率改善。不能把 Token 数当订阅额度或账单成本。没有合格 standard 样本时结论就是「未形成有效配对」，不能把 routine 任务硬算成 Terra 任务。禁止修改生产 `SKILL.md`、全局 Codex home、Verifier、Claude 审核、CCB 或现有测试基线。

### 与上轮不同的控制变量

**同一版 magent skill + 相同主 Agent + 同名 standard 角色 + 相同 Verifier**。仅在两个隔离 CODEX_HOME 的 `agents/sol-advisor-standard.toml` 修改模型和 effort：

| 项目 | control | terra |
| --- | --- | --- |
| 主 Agent | gpt-6.1-sol/high | gpt-6.1-sol/high |
| Worker 路由名 | sol_advisor_standard | sol_advisor_standard |
| standard 的模型 | gpt-6-luna/xhigh | gpt-6.1-terra/high |
| routine/high Worker | 当前模板不变 | 当前模板不变 |
| 独立 Verifier | sol_verifier_routine（Luna/xhigh） | 相同 |
| Skill、契约、验收脚本、代码基线 | 相同 | 相同 |

因此得到的是**模型+effort组合**的比较，不是只改变模型架构的因果效应，也不是「两者相同 effort」比较。不要将 control 环境运行 `tools/check_role_registration.py` 视为错误——该静态检查设计上要求标准角色恒为 Terra，会拒绝本次**有意**替换；改用本文准备工具的 `verify` 验证两臂差异。

## A. 挑 6～8 个候选任务（不执行实现）

优先选历史上有真实多模块协调难度、但不是密码学/深层并发/权限安全/破坏性迁移的任务；每个任务有可重放的修复前 commit，固定验收脚本，隐藏答案，不含生产凭据。

建议候选池（不是已准备好的业务代码夹具；实际样本由本机仓库冻结）：

| 编号 | 中等任务候选 | 正常 standard 的依据 | 排除原因 |
| --- | --- | --- | --- |
| T01 | 管理 API 新增字段与服务/持久层兼容迁移 | 接口和数据层协同、向后兼容测试 | 只是改一个 DTO |
| T02 | 事件接收、持久化、异步重试与幂等规则联调 | 跨服务/存储/测试的交互约束 | 涉及金融结算关键不变量 |
| T03 | 前后端上传流程、批量校验和错误回传 | 多模块契约协调、错误可恢复 | 只有单函数 CSV 解析 |
| T04 | 现有异步任务 API、状态持久化和查询接口联动 | 业务流程跨边界、状态建模 | 存在难以验证的隐含并发竞态 |
| T05 | 多数据源配置、缓存失效和查询接口行为一致 | 非平凡但有既有模式的重构 | 实际只修一个配置常量 |
| T06 | API 分页/过滤/权限外的兼容策略升级 | 参数、排序、兼容性和集成测试耦合 | 已有完整可照抄实现 |
| T07 | 管理后台批量操作与后端幂等更新 | UI/API/服务联动、多种部分失败状态 | 真实生产数据库不可隔离 |
| T08 | 跨平台 CLI 的账户导入/导出、配置和回归测试 | CLI、持久层、错误契约共同变化 | 涉及真实 token/凭据 |

候选说明不得诱导模型：「请使用 Terra」「必须 standard」「这是中等任务」「为了测试强制代理」。难度标签与 prompt 在预筛选前冻结。先做代码所有者/验收者人工检查：任务确实需要多个模块之间的**设计或接口决策**，不是只因涉及多个文件才升档。不得选择已知道答案并在 prompt 中透露的案例。

记录 `docs/terra-trial-candidates-template.csv` 的所有候选（包括没选中的）；冻结 `base_sha`、`prompt_sha256`、验收脚本 SHA 和预期可改目录。**不能因后续 A/B 结果不好再剔除案例**。

### 无实现的路由预筛选

对 6～8 个冻结任务，只要求本机主 Agent 读取相同 skill 和必要上下文后声明路由，不允许编辑、Worker 派发或 Verifier：

    $magent 请根据本次任务及仓库结构，只执行路由选择，不开始实现/验证/派发；输出 SELECTIVE ROUTE、target_agent、具体依据，然后停止。任务需求：<粘贴冻结 prompt>

这是一项额外的**预筛选**，不是正式比较中的路由声明。记录 `preflight_route`，不许后改。最多选择前 4 个自然选择 `sol_advisor_standard` 且无 high 风险的候选；如果只找到 2～3 个，可如实执行 2～3 对探索，不能冒充 4 对；如果 0 个，**立即停止**，回传预筛选记录讨论路由政策，而不是强迫运行或静默改模型。达到 4 个后不必继续消耗预筛选额度。预筛选可用 `codex exec --sandbox read-only --json`，实际是否只做路由需从输出核对。

## B. 构建两套实验环境（不覆盖日常 Codex）

在 magent clone 的根目录执行（Python 3.9+；macOS/Linux）：

    git rev-parse HEAD
    python3 tools/test_terra_trial.py
    python3 tools/prepare_terra_trial.py prepare --source . --out ../terra-trial-homes
    python3 tools/prepare_terra_trial.py verify --out ../terra-trial-homes

脚本生成：

    ../terra-trial-homes/
      control/config.toml
      control/agents/*.toml
      control/skills/magent/...
      terra/config.toml
      terra/agents/*.toml
      terra/skills/magent/...
      trial-lock.json

两份 skill 是**原样复制**，两个 home 只有实际安装的 standard 角色模型与 effort 不同；不复制认证文件，不调用网络，不修改当前项目，若目标目录已存在则拒绝覆盖。Windows 在 PowerShell 用 `py -3` 和相应 Windows 路径。准备脚本不是子 Agent 自动注册的完整安装器；`config.toml` 仅写五个角色的注册片段。

把你日常使用的**非秘密**模型 provider、审批、sandbox、trust 配置一致地合并到两份 config.toml；认证依各自环境完成，**不要把 auth.json、令牌、完整配置或用户路径上传到报告**。先确认 `codex --version`、账户可以实际调用三种模型，并核对两个注册文件未被后续自动覆盖。两组主 Agent 必须用相同的启动参数显式指定 `gpt-6.1-sol / high`。

## C. 8 次正式运行（先做 2 对，再决定是否跑 2 对）

每个入选案例必须有两个**独立的干净代码克隆**，从同一 `base_sha` 创建，隐藏修复答案且两个副本原始状态一致。使用完全相同的任务 prompt（直接调用 `$magent <冻结需求>`，不要写 Worker 模型要求），相同验收命令。不得在 control 运行后复用其工作目录给 terra。

执行模板（两个 CASE_DIR 指向分别克隆的**不同**目录）：

    CODEX_HOME="<绝对路径>/terra-trial-homes/control" \
      codex exec -C "<control-case-dir>" -m gpt-6.1-sol \
      -c 'model_reasoning_effort="high"' --sandbox workspace-write \
      --json - < "<case-prompt-file>" > "T01-control.jsonl" 2> "T01-control.stderr"

    CODEX_HOME="<绝对路径>/terra-trial-homes/terra" \
      codex exec -C "<terra-case-dir>" -m gpt-6.1-sol \
      -c 'model_reasoning_effort="high"' --sandbox workspace-write \
      --json - < "<case-prompt-file>" > "T01-terra.jsonl" 2> "T01-terra.stderr"

PowerShell 使用 `$env:CODEX_HOME="<绝对路径>"`，以 `Get-Content -Raw ... | codex exec ... -` 传入 prompt。脚本不会自动启动任何模型请求，也不包含任何自动付费/自动重试逻辑。建议奇数任务 control→terra、偶数任务 terra→control；不要在两组互相污染的进程中共享工作目录。

模型退出后，操作者在各自副本运行固定不可修改的验收命令并保存退出码、文件 diff 和验收脚本 checksum。不要因模型自述 PASS 而判 pass。确认 Verifier 是独立 Luna/xhigh；核对 **sessions / JSONL 中 standard Worker 实际派发次数 ≥1 且模型匹配**。出现 high/routine 额外修复 Agent 时标为模型混淆，留证但不要据此单独归因标准模型。

### 三个停止门

1. **路由门**：若正式运行中首次声明不是 standard，或实际上没派发 standard，标记为路由失配，不计入有效成对比较；不能在结果差时偷偷补写「使用 Terra」。
2. **安全/质量门**：越界修改、未完成独立验收、自动 Claude、角色无权限、主 Agent 验收、凭据泄露 -> 停止并修复评测设施。
3. **预算门**：只跑前两对（4 次正式执行）；检查两组都能自然标准路由、质量和用量字段均可核实，再做剩余两对。遇到 usage limit / operator timeout：保存原始失败，恢复后使用同任务 resume 或重建干净同基线副本补跑，清楚写出选择；不把两次当作不同正式成功样本。

## D. CSV 和自动报告

填写 `docs/terra-trial-results-template.csv`（4 个任务 × 2 行），字段：

    case_id,arm,project,base_sha,prompt_sha256,initial_route,standard_calls,observed_model,observed_effort,verifier_model,verifier_effort,verdict,acceptance_pass,elapsed_sec,input_tokens,output_tokens,retries,worker_calls,verifier_calls,notes

只有 `initial_route=standard`、`standard_calls>=1`、模型和 Verifier 均实测匹配，且两行 project/base/prompt hash 相同才进入可比分析。若 standard 启动后有 high Worker 救场，须在 notes 标明——这类结果不能视为单纯标准模型效果。

    cp docs/terra-trial-results-template.csv ../terra-trial-results.csv
    # 填完八行，不保留空白模板行
    python3 tools/analyze_terra_trial.py ../terra-trial-results.csv \
      --out ../terra-trial-report.md

脚本会报告有效配对、失败/违规排除理由、通过数、活跃耗时、已知重试、完整 Token 配对。缺失值保留空白，不视作零；没有证据不得填充实际派发模型。所有类别包括失败结果均须保留，避免按结果选择样本。

## E. 决策门槛：优先安全与可复现，暂不宣布收益

- 先看 **4 对质量**：至少不出现 Terra 独有的验收失败、scope 违规、Verifier 非法绕过；否则不扩大实验。
- 有完整耗时和 token 的配对才比较效率。对照同样验收规则，记录 Worker/Verifier 派发与重新尝试次数；避免仅看总耗时忽略多次审查。
- Terra 全部通过且方向上耗时/重试/token 更优，只能判为「值得扩大到真实业务样本」，**不能自动改 production skill**。
- 如果连自然选择 standard 的样本都难以找到，则优先修正**任务划分和路由可观察性**，而不是直接改模型。
- 原始代码、测试、模型版本和数据报告冻结。可回传压缩的脱敏证据及选出的四个任务，不需要把所有 Codex JSONL 原样上传。

## F. 回传以便我做下一轮精确修改

1. 完整候选筛选 CSV（含未被选的任务、natural route、依据）。
2. 八行 `terra-trial-results.csv` 和 `terra-trial-report.md`（或如实的部分结果）。
3. 任务冻结清单：base_sha、prompt_sha256、实际验收命令/脚本 SHA、修改 ownership。
4. 每次的实际 Worker/Verifier 角色与模型证据、验收退出码、摘要日志；失败、额度中断单独保留。
5. `trial-lock.json`、`codex --version` 和脱敏配置差异摘要。

**本轮只调整实验工具和文档，不调整用户正在使用的主 Agent、`SKILL.md` 路由阈值、角色默认模型、CCB 或现有 verifier gate。**
