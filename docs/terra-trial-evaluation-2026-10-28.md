# Terra Standard 专项对照：两对先行测试完成后的收敛判断

日期：2026-10-28  
实验锁定版：`f236e66b4d5a686aae31815fe6b331359cdc2eec`  
状态：**先行 2 对/4 次正式运行完成；原计划 4 对/8 次未完成，不扩大样本；不发布优化收益。**

## 数据来源和证据边界

本记录依据使用者本地回交的 `terra-trial-results.csv`、`terra-trial-report.md`、`terra-trial-candidates.csv`。针对 CSV 的配对关系、声明路由、实际 Worker 模型与 effort、Verifier pin、PASS、返工、用量缺失作了独立一致性检查。

没有附带的 `terra-trial-evidence/formal/*` 原始会话 JSONL、验收输出、冻结输入及审计 JSON，因此文中「测试通过」「没有源码变更」「会话派发」为**交付报告与 CSV 的一致记录**，不是在本审核环境中重放的验证。正式 `magent` 与业务工程源码未随本记录修改。

## 结果

| 指标 | Luna / xhigh control | Terra / high experiment |
| --- | ---: | ---: |
| 有效质量配对 | 2/2 PASS | 2/2 PASS |
| 返工（Verifier FAIL 导致） | T03 0；T04 1 | T03 0；T04 1 |
| T03 活跃 CLI 耗时 | 640.926s | 542.030s |
| T04 活跃 CLI 耗时 | 878.708s | 1137.450s |
| 两对算术平均活跃耗时 | 759.817s | 839.740s |
| 完整 Token 观测 | T03/T04 | **仅 T03** |

- **T03**：两臂自然派发 `sol_advisor_standard`，每臂实际调用一个 standard Worker 与一个新上下文 Luna/xhigh Verifier；独立验收最终 PASS、返工 0。Terra 记录耗时比 Luna 少 98.896 秒（约 15.4%），但这只是一个样本；完整输入+输出 Token 分别为 Luna **4,391,476**、Terra **4,315,423**。
- **T04**：两臂均 natural standard 路由，两个版本的初次独立 Verifier 均发现不同的会话/输入历史隔离语义缺陷；各由同档 Worker 进行一次定向返工，随后新的独立 Verifier PASS。Luna 活跃耗时 **878.708s**，Terra 原始运行加同线程恢复活跃耗时 **1137.450s**。Terra 的原始配额中断与一个无模型输出/无用量的 Verifier 派发均已记载，**聚合 Token 缺失**；不得用 0 替代，或把挂起等待时间与活跃 CLI 时间混为一谈。
- 两组主 Agent 均固定 Sol/high；实际标准 Worker 对照为 Luna/xhigh 与 Terra/high；普通独立 Verifier 固定 Luna/xhigh。对比的是**模型及 effort 的组合**，不是纯模型身份差异。所有真实业务修改发生在隔离的测试工作树中；本报告不声称生产仓库已修改。

## 路由预筛选事实

- 候选池共审阅 8 项，但只有 **4 项**实际完成了只声明路由、未实施的模型预筛选。
- T01 选择 high Worker/Verifier，T02 选择 standard Worker 但要求 high Verifier；均因不能控制相同普通 Verifier 排除。
- T03/T04 的路由和 Verifier 均符合 standard + routine，对照实际调用与相同冻结基线通过。
- T05–T08 因安全风险、不可冻结验收或任务过小，被人工资格检查排除，**没有模型路由观测**。没有第 3、第 4 对可直接续跑的合格样本。

因此不能写成「8 项都经模型筛选」或「4 对已验证」。

## 为什么不将本轮结果解释为优化收益

1. 只有 2 个真实配对，全部 PASS 说明可调用且能在独立复验后实现需求，不说明成功率优势。
2. T04 两臂都有一次独立验收 FAIL，可靠性没有观察到明确差异；T04 的 Terra 中断还影响时间口径。
3. 只有 T03 有完整 Token 配对，不能据此估算总体输入、输出、账单或订阅额度。
4. 浏览器 Web 端的静态接线/类型检查不是浏览器端到端验证；对照试验不应夸大功能覆盖范围。
5. 动态实验过程中出现隔离环境 trust 记录漂移，最终已纠正，但整个测试时段不能说配置字节完全相同。

## 决策和后续操作

**当前决策：保留生产 magent 配置，结束这次已完成的两对先行测试。不追加无资格样本，不重复已通过的 T03/T04，不修改 `SKILL.md`、`roles/*.toml`、Verifier、Luna/Terra/Sol effort、CCB、长任务状态工具。**

对 `gpt-6.1-terra/high` 的评价从「仅故障升级通路已证明可用」更新为「至少在两个实际中等复杂工程任务里，作为 standard Worker 能通过最终独立验收，但相对于 Luna/xhigh 的效率收益仍未证实」。

后续**不作为这次测试未完成的强制补跑要求**；只有当新出现符合路由条件且具备冻结 oracle 的真实任务时，才用新案例开展 2～4 对额外观测，并保持原样参数。不要根据测试输赢改选候选任务。

## 追溯说明

私有代码基线、prompt SHA、配额中断证据与会话审计仍在用户本地 `terra-trial-evidence/`，本仓库只存不含凭据的汇总结论。该文档是评审记录，不是独立认证、统计显著结论或性能上线许可。
