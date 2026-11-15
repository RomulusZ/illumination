# 模块边界禁令

## 1. 包位置

- 顶层包：`illumination`，代码位于 `src/illumination/`。
- 子包：`illumination.types`、`illumination.config`、`illumination.repo`、
  `illumination.service`、`illumination.runtime`、`illumination.ui`。
- 不要把子包放到 `src/` 顶层（如 `src/types`）：那会变成顶层模块 `types`，
  与标准库同名并互相覆盖。
- 测试导入统一使用 `from illumination.<layer> import ...`。
- `src/illumination/__init__.py` 只放 `__version__`，不做跨层 re-export。

## 2. 分层与依赖方向

唯一允许的方向：

```text
Types → Config → Repo → Service → Runtime → UI
```

| 层 | 允许 import 的内部层 | 禁止 import |
|---|---|---|
| `illumination.types` | `illumination.types` | 其余所有层 |
| `illumination.config` | `types`、`config` | `repo`、`service`、`runtime`、`ui` |
| `illumination.repo` | `types`、`config`、`repo` | `service`、`runtime`、`ui` |
| `illumination.service` | `types`、`config`、`service` | `repo`、`runtime`、`ui` |
| `illumination.runtime` | `types`、`config`、`repo`、`service`、`runtime` | `ui` |
| `illumination.ui` | `types`、`config`、`service`、`runtime`、`ui` | `repo` |

说明：

- `types` 是最底层，不能依赖任何其他层。
- `service` 不得直接 import 具体 Repo 实现；外部能力通过 `types.ports` 注入。
- `ui` 不得 import Repo；Agent run 必须走 Runtime，同步查询/短命令可走 Service。
- 同层模块可以互相依赖，但必须无环；跨子域优先通过 Protocol 解耦。

## 3. 端口与适配器

端口定义在 `src/illumination/types/ports.py`：

```python
class LLMPort(Protocol): ...
class ClockPort(Protocol): ...
class ArtifactStorePort(Protocol): ...
```

- 端口只能使用领域类型与基本类型，不能暴露 HTTP、数据库、Settings、LangGraph 状态等基础设施类型。
- `repo` 实现端口；`service` 只依赖端口；`runtime` 是组合根，实例化适配器并注入。
- 新增外部能力时，先加 Port，再加 Adapter，最后在组合根装配；顺序不可颠倒。

当前适配器：

| Port | 适配器 | 文件 |
|---|---|---|
| `LLMPort` | `OpenAICompatibleLLM`、`StubPlannerLLM` | `repo/llm_adapter.py`、`repo/stub_llm.py` |
| `ClockPort` | `SystemClock` | `repo/system_clock.py` |
| `ArtifactStorePort` | `FileArtifactStore` | `repo/file_artifact_store.py` |

## 4. 组合根

- 只有 `runtime/composition.py` 可以实例化具体 Repo / LLM / store。
- `service` 通过构造函数或函数参数接收端口。
- 禁止在模块顶层创建全局客户端、全局 LLM、全局 store 实例。
- 禁止在 `__init__.py` 中跨层 re-export 或执行副作用。

## 5. 各层职责

| 层 | 允许 | 禁止 |
|---|---|---|
| `types` | Pydantic 模型、Enum、TypedDict、type alias、Protocol、纯校验 | 网络、数据库、文件 I/O、业务决策、import 上层 |
| `config` | 环境变量、密钥、路径、默认值、跨字段校验 | 网络/数据库 I/O、业务判断 |
| `repo` | HTTP/DB/对象存储/时钟等适配器、重试、退避、分页、外部数据映射到 Types | 领域规则、prompt 组装、编排 |
| `service` | 领域规则、结构化输出校验、用例、校验 | 建图、直接连数据库/HTTP、返回 HTTP 响应 |
| `runtime` | 图编排、上下文装配、预算、HITL、checkpoint、组合根 | 写领域规则、直接做业务判断 |
| `ui` | 参数校验、错误码转换、同步查询/短命令；Agent run 通过 Runtime | 直接 import Repo、在入口写业务逻辑 |

## 6. 同层依赖与循环

- ✅ 同层模块可以互相依赖。
- ❌ 禁止循环依赖。
- 跨子域优先通过 `types/ports.py` 中的 Protocol 解耦。
- 循环依赖检测由 `lint/arch_checker.py` 负责。

## 7. `__init__.py` 规则

- `src/illumination/__init__.py` 只放 `__version__`。
- 子包 `__init__.py` 只做同层 re-export。
- 禁止在 `__init__.py` 中读取环境变量、创建客户端、执行网络或数据库操作。

## 8. 外部依赖建议

| 层 | 建议允许的外部依赖 |
|---|---|
| `types` | `pydantic`、标准库 |
| `config` | `pydantic-settings`、`pydantic` |
| `repo` | `httpx`、`tenacity`、数据库/对象存储 SDK |
| `service` | 纯 Python；不引入 I/O 客户端 |
| `runtime` | `langgraph`、`langchain-core` |
| `ui` | `argparse`、未来的 `fastapi` / `typer` |

## 9. Linter 规则

`lint/arch_checker.py` 逐条执行以下规则；CI 阻断：

| 规则编号 | 内容 | 失败级别 |
|---|---|---|
| ARCH-000 | Python 文件无法读取或解析 | 阻断 |
| ARCH-001 | 违反分层方向 | 阻断 |
| ARCH-002 | `service` import `runtime` 或 `ui` | 阻断（由 ARCH-001 覆盖） |
| ARCH-003 | `service` import `repo` | 阻断 |
| ARCH-004 | `ui` import `repo` | 阻断 |
| ARCH-005 | 依赖有环 | 阻断 |
| ARCH-006 | `__init__.py` 跨层 re-export | 阻断 |
| ARCH-007 | 受控层动态 import | 阻断 |
| ARCH-008 | `tests/`、`scripts/`、`lint/` 豁免 | 豁免 |
| ARCH-009 | `TYPE_CHECKING` 导入按普通依赖处理 | 阻断 |

实现说明：

- 包目录定位优先选择 `src/` 下同名的 `illumination/`，避免仓库根目录也叫
  `illumination` 时误判模块名；无 `src/` 时退回最后一个同名目录（供测试 fixture 使用）。
- 支持子模块前缀、相对导入与 `from illumination import service`。
- 违规输出文件、行号、规则编号与违规 import。

## 10. 例外

例外必须先写 ADR，并在本节登记原因与失效条件；不允许口头例外。

## 11. 唯一事实源

本文件是分层与导入边界的唯一事实源。`docs/agents.md` §3 只保留摘要与本文件链接。
