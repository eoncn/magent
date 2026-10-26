# Wiki 维护日志

## [2026-10-26] ingest | 首次生成项目知识库

**触发**：用户执行 `/init-wiki`。

**操作**：
- 完整读取 `SKILL.md`、`README.md`、`references/external-review.md`、`.gitattributes`、`.gitignore`。
- 完整读取 `scripts/` 全部 7 个文件（`claude-review.sh`、`review_runner.py`、`review_support.py`、`review_result.py`、`review_windows.py`、`test_review_runner.py`、`test_sync_review.py`）。
- 完整读取 `tools/` 全部 3 个文件（`install.py`、`sync.py`、`test_maintenance.py`）。
- 完整读取 `roles/` 全部 4 个 TOML 模板。

**生成页面（10 个）**：
1. `paradigm.md` — LLM Wiki 范式规范
2. `overview.md` — 项目总览
3. `skill-routing.md` — 技能路由（SKILL.md Section 1-2）
4. `verification-gate.md` — 独立验证门（Section 3）
5. `external-review.md` — 外部 Claude/CCB 审核（Section 4）
6. `roles.md` — 角色模板
7. `review-runner.md` — 监督器实现
8. `review-support.md` — 仓库指纹
9. `review-result.md` — 输出校验
10. `cross-platform.md` — 跨平台执行
11. `install-sync.md` — 安装与同步
12. `testing.md` — 离线测试体系

**索引与日志**：
- `index.md` — 按类别分组的导航索引，列出全部 12 个内容页。
- `log.md` — 本文件。

**CLAUDE.md 同步**：
- 新增 `## Repo Wiki — Mandatory Knowledge Base` 章节，含文件路径 → Wiki 页面映射表。

**质量检查**：
- 交叉引用：每页底部"相关页面"链接指向已生成文件。
- 索引：`index.md` 列出全部 12 个内容页。
- 路径：所有源文件引用均经 `Read` 验证存在。
- 无孤立页面：每个内容页至少被 `index.md` 与至少一个其他内容页引用。
- 信息准确性：角色模型/effort、退出码、环境变量、命令行参数均来自源码读取，未猜测。