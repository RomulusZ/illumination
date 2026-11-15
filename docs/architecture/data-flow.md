# 数据流与状态

## 1. 一次 run 的链路

```text
CLI / API
  → RunRequest
  → Runtime.run
  → 图节点 plan：PlanningService → LLMPort → 结构化输出校验 → RunPlan
  → 图节点 finalize：ArtifactStorePort.put("plan.json") → ArtifactRef
  → RunResult（status / plan / outcomes / artifacts / 时间）
```

## 2. 状态契约

图状态 `RunState`（`runtime/pipeline.py`）只承载：

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | `str` | 稳定运行标识 |
| `request` | `RunRequest` | 原始目标与预算 |
| `started_at` | `datetime` | 时区感知 |
| `plan` | `RunPlan` | 结构化计划 |
| `outcomes` | `list[StepOutcome]` | 每步观察结果 |
| `artifacts` | `list[ArtifactRef]` | 产物指针 |
| `status` | `RunStatus` | `running` / `partial` / `succeeded` / `failed` |
| `error_code` | `str` | 稳定错误码 |
| `finished_at` | `datetime` | 时区感知 |

约束：

- 正文、完整 prompt、原始外部响应、完整 trace 不进状态。
- 需要跨节点共享的大对象必须落 artifact store，状态里只留 `ArtifactRef`。
- 并行字段必须声明 reducer；同 key 不同值抛错，不静默覆盖。
- 时间只来自 `ClockPort`，禁止在节点内直接读系统时钟。

## 3. Artifact 契约

`ArtifactRef` 至少包含：

```text
artifact_id  内容寻址标识（art_<sha256 前 16 位>）
path         存储位置
sha256       内容摘要
summary      人类可读摘要
```

`FileArtifactStore` 的写入规则：

- 只接受纯文件名，拒绝路径分隔符与 `..`；
- 同名不同内容生成不同 `artifact_id`，不覆盖既有产物；
- `read` 必须能由 `artifact_id` 唯一解析到文件。

## 4. 失败分类

| 分类 | 当前例子 | 行为 |
|---|---|---|
| 领域错误 | 计划超出步骤上限、输出不符合 schema | `failed` + 稳定 `error_code` |
| 适配器瞬时错误 | 429、5xx、连接错误 | 有界指数退避；耗尽后失败 |
| 适配器确定性错误 | 400、响应结构无效 | 不重试，直接失败 |
| 存储失败 | artifact 无法写入 | `failed` + `artifact_store_unavailable` |
| 未实现能力 | 执行器缺失 | `partial` + 步骤 `skipped` |

禁止把上述状态合并成“成功”或“空结果”。

## 5. 未来扩展

接入执行器时：

1. 在 `types/ports.py` 增加工具 Port；
2. 在 `repo/` 实现 Adapter；
3. 在 `runtime/pipeline.py` 增加执行节点；
4. 在 `runtime/composition.py` 装配；
5. 在 `tests/runtime/` 增加分支与失败用例；
6. 若引入并行，先为本文件补 reducer 声明，再写代码。
