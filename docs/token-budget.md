# Token 预算与降级

骨架阶段尚未实现计量器；本文件定义接入时必须遵守的契约，避免后续各模块各自记账。

## 1. 预算层级

| 层级 | 配置项 | 作用域 |
|---|---|---|
| Run | `ILLUMINATION_RUN_TOKEN_BUDGET` | 一次 `RunResult` |
| Call | `ILLUMINATION_CALL_TOKEN_BUDGET` | 单次 LLM 调用 |
| Step | `ILLUMINATION_MAX_STEPS` | 计划步骤数上限 |

## 2. 单写原则

- token、工具调用次数、墙钟、修复次数由 harness 写入，模型不可写。
- 计量事件必须落在运行产物或审计账本，不只存在于内存。
- 并行分支的预算字段必须声明 reducer；同 key 不同值抛错。

## 3. 计量字段

一次调用至少记录：

```text
run_id
step_id
attempt
model
input_tokens
output_tokens
cached_tokens
elapsed_ms
error_code        # 可选
```

一次 run 至少记录：

```text
run_id
status            # succeeded / partial / failed
total_tokens
step_count
repair_count
degradation_level
```

## 4. 降级阶梯

按顺序执行，不得跳级，且必须写入产物：

| 级别 | 动作 | 触发条件 |
|---|---|---|
| L0 | 正常执行 | 预算充足 |
| L1 | 缩减上下文，保留指针与摘要 | 接近 call 预算 |
| L2 | 降低步骤数或合并步骤 | 接近 step 预算 |
| L3 | 跳过非关键步骤，标记 `partial` | 接近 run 预算 |
| L4 | 终止并返回已得结果 + 缺口清单 | 预算耗尽 |

## 5. 禁止事项

- 用重试掩盖预算耗尽。
- 预算触顶后继续静默调用。
- 只把预算写进日志，不写进产物。
- 让模型自报 token 或成本。

## 6. 接入顺序

1. 在 `types/` 增加 `TokenEvent` 与 run 级汇总契约；
2. 在 `service/` 增加纯计量函数；
3. 在 `runtime/` 的节点前后写事件；
4. 在 `RunResult` 或独立 artifact 中输出汇总；
5. 为 L1–L4 每一级写测试。
