# 命名与编码规范

PEP 8 是基线；`pyproject.toml` 中固定的工具配置优先于个人偏好。

## 1. 基础规则

- 包与模块：全小写 `snake_case`。
- 类：`CapWords`；异常以 `Error` 结尾。
- 函数、方法、变量：`lowercase_with_underscores`。
- 常量：`UPPER_SNAKE_CASE`。
- 私有名称：单下划线前缀；不使用双下划线名称改写。
- 导入顺序：标准库 → 第三方 → 本项目；禁止通配符导入。
- 项目内使用绝对导入：`from illumination.types import ...`。
- 公开模块、类与函数必须有 docstring；公开函数必须有类型标注。
- 禁止尾随空格、制表符混用与拼音命名。

## 2. 分层命名

| 层 / 角色 | 后缀或前缀 | 示例 |
|---|---|---|
| 领域契约 | 无后缀 | `RunRequest`、`RunPlan`、`RunResult` |
| 端口 / Protocol | `Port` | `LLMPort`、`ClockPort`、`ArtifactStorePort` |
| 外部适配器 | `Adapter` 或具体实现名 | `OpenAICompatibleLLM`、`FileArtifactStore` |
| Service 用例 | 动词 / `Service` | `plan_run`、未来的 `ExecutionService` |
| Runtime 图 / 节点 | `Graph` / `Node` | `build_run_graph`、`plan_node` |
| UI 请求 / 响应 | `Request` / `Response` | `RunRequest`、未来的 `RunStatusResponse` |
| 异常 | `Error` | `IlluminationError`、`ArtifactStoreError` |
| 测试 | `test_` 前缀 | `test_run_once_failure_is_typed` |

补充：

- Repo 层里，存储实现叫 `*Store` / `*Repo`，外部系统适配器叫 `*Adapter`。
- Service 不叫 `Manager`、`Helper`、`Util`、`Processor`。
- Runtime 入口用 `build_*` / `run_*`，不用 `handle_*`。
- Agent 相关文件使用 `agent_` 前缀。

## 3. 契约命名

固定名称包括：

```text
RunRequest
RunPlan
PlanStep
StepOutcome
RunResult
RunState
ArtifactRef
LLMRequest
LLMResponse
LLMUsage
```

约定：

- 模型类不加 `Model`、`Schema`、`DTO` 后缀。
- 字段 `snake_case`，与 JSON 契约一致：`run_id`、`step_id`、`artifact_id`、`sha256`。
- 不单独使用 `id`；写清对象。
- 布尔字段用 `is_`、`has_`、`can_`、`should_` 前缀。
- 计数字段用复数或 `_count` / `_n`。
- 时间字段用 `_at` / `_date`；路径用 `_path` / `_root` / `_dir`；
  摘要用 `_hash` / `_digest` / `_sha256`。

## 4. 函数与变量

- 函数以动词开头：
  - `build_` 构造内存对象；
  - `load_` 从本地读取；
  - `fetch_` 从外部获取；
  - `parse_` 把原始输入转为结构化数据；
  - `plan_`、`run_`、`execute_`、`verify_`、`render_` 领域动作；
  - `to_` / `from_` 模型转换。
- 避免 `get_` 泛用；本地用 `load_`，远端用 `fetch_`。
- 避免 `data`、`info`、`obj`、`item`、`tmp`、`foo`、`bar` 作为业务变量。
- 捕获后重抛使用 `raise ... from exc`。

## 5. 环境变量与错误码

- 自定义环境变量统一 `ILLUMINATION_*`；外部服务沿用 `OPENAI_*`、`LANGSMITH_*`。
- 只允许通过 `Settings` 读取，禁止散落 `os.environ`。
- 错误码使用稳定的 `UPPER_SNAKE_CASE` 或 `snake_case` 字符串，例如
  `structured_output_invalid`、`artifact_store_unavailable`、`configuration_invalid`。
- 枚举值使用 `UPPER_SNAKE_CASE`，与 JSON 显式映射：
  `RunStatus.PARTIAL = "partial"`。

## 6. 文件名与目录名

- 文件 `snake_case.py`，与主要职责对应。
- 目录全小写：`types/`、`config/`、`repo/`、`service/`、`runtime/`、`ui/`。
- 测试目录镜像源码：

```text
src/illumination/service/planning.py  →  tests/service/test_planning.py
src/illumination/repo/llm_adapter.py  →  tests/repo/test_llm_adapter.py
```

## 7. 注释与文档

- 注释解释“为什么”和约束，不复述代码。
- 禁止用注释保留死代码。
- `TODO` 必须写清触发条件或责任人。
- 文档与代码冲突时，先确认事实源：架构边界看
  [boundaries.md](../architecture/boundaries.md)，其余以测试与代码为准。
