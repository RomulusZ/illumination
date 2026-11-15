# 架构决策记录（ADR）

本目录保存 Illumination 的架构决策。每个 ADR 至少包含：背景、决策、替代方案、后果、失效条件。

| ADR | 标题 | 状态 |
|-----|------|------|
| [0001](0001-src-illumination-package-layout.md) | `src/illumination` 包布局与分层 | Accepted |

约定：

- 编号连续，四位数；文件名使用 `NNNN-kebab-case-title.md`。
- 决策改变时新增 ADR，不覆盖旧 ADR；旧 ADR 标记 `Superseded by NNNN`。
- 例外（分层、依赖、契约破坏、私有数据边界）必须写 ADR，不能只写在 PR 描述里。
- 新增 ADR 后同步本索引，并在 `docs/agents.md` 登记。
