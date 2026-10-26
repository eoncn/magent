# Repo Wiki 索引

> `magent` 项目知识库导航。按类别组织，每页一行摘要。
> Wiki 范式见 [paradigm.md](paradigm.md)。维护日志见 [log.md](log.md)。

## 范式

- [paradigm.md](paradigm.md) — LLM Wiki 范式规范（持久/复利/LLM 维护的知识库）

## 架构与核心

- [overview.md](overview.md) — 项目身份：Codex skill、三步独立、默认关闭外部审核、状态外置
- [skill-routing.md](skill-routing.md) — SKILL.md Section 1-2：SELECTIVE ROUTE 声明、四种路由、worker 委托契约
- [verification-gate.md](verification-gate.md) — SKILL.md Section 3：独立 verifier 只读验证门、<30 行报告、FAIL 修复循环
- [external-review.md](external-review.md) — SKILL.md Section 4：opt-in 规则、同步/后台模式、生命周期子命令、回调协议、verdict action
- [roles.md](roles.md) — 四个具名角色模板（routine/high × advisor/verifier）与模型配置

## 实现模块

- [review-runner.md](review-runner.md) — `scripts/review_runner.py` 监督器：create/launch/worker/control 生命周期
- [review-support.md](review-support.md) — `scripts/review_support.py` 仓库指纹：HEAD+索引+跟踪+非忽略未跟踪+子模块
- [review-result.md](review-result.md) — `scripts/review_result.py` CCB envelope + JSON schema + verdict 一致性校验

## 基础设施

- [cross-platform.md](cross-platform.md) — Windows Job Object / 字节范围锁 / Git Bash / MSYS 参数转换 / junction
- [install-sync.md](install-sync.md) — `tools/install.py` 链接 Codex home + `tools/sync.py` SSH 快进远端
- [testing.md](testing.md) — 三套离线测试：maintenance / review_runner / sync_review

## 元数据

- 生成时间：2026-10-26
- 页面数量：12（不含本索引与 log）
- 源仓库：`/home/eoncn/otherWorks/magent`
- 主分支：`master`（PR 目标 `main`）