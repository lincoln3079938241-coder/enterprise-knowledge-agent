# 02 · 方案与动态决策

## 架构

- `src/main.py`：命令行入口与文件保存。
- `src/model.py`：真实 LLM Chat Completions Function Calling；通过环境变量选择模型/兼容服务。
- `src/agent.py`：循环询问 LLM 下一动作，执行工具并回传观察，设步数与工具调用上限；记录结构化 Trace。
- `src/tools.py`：本地关键词/同义词检索、文档原文读取、引用检查与基于合成字段的矛盾防护。
- `src/mock_model.py`：可重复的脚本模拟器，仅做流程冒烟测试，**不是**满足真实模型决策要求的替代品。

## 决策循环（实时模式）

1. 用户问题及可选历史进入模型上下文；模型从 `search_documents`、`read_document`、`submit_answer` 三类动作中选择。
2. 工具返回 ID/摘要、原文或错误。执行器把完整结果追加为带对应调用 ID 的 `tool` 消息，**继续发起下一次 LLM 请求**；Trace 另存完整结果与短摘要。
3. 模型观察：可读取另一个文档、尝试改写检索、对失败进行有限重试，或提交答案。
4. 若模型同轮提出多个工具，执行器不预先执行该批动作，而是为每个调用返回协议错误，要求模型观察后在下一轮只选一个工具；这避免把并行提议伪装成逐步 ReAct。
5. `submit_answer` 的结果由执行器进行**确定性事实防线**校验：文档 ID 曾由成功检索返回、原文已实际读取、引文是原文子串；一旦读取搜索结果中的某个冲突候选，必须读取并引用同主题的其他冲突候选。`not_found` 也必须发生在成功检索之后。不通过则反馈错误给模型并允许修正。
6. 达到 `max_steps` 或 `max_tool_calls` 的上限时，执行器输出 `incomplete`，不会把中途结果伪装成完成；`max_tool_calls` 统计搜索、读取、结构化提交及未知工具调用的实际执行次数。

> 检索、证据约束、步数限制采用确定性代码是安全边界；**下一步选择什么业务动作**在实时模式中由模型决定。脚本 Mock 模式则仅按测试规则推进，明确与实时模式区分。

## Token 控制

原生 SDK，避免复杂框架对象及长提示；文档检索先给 ID 与短摘要，按需逐个读取完整原文；合成文档条款短、回答限定于相关事实；不调用外部搜索/Embedding。API 用量记录在 `token_usage_reported`，如服务商不返回 usage，记录为 0（并非实际零消耗）。

## 框架取舍

原生 Agent loop + `openai` SDK（`requirements.txt` 指定 >=1.60,<3）；使用 `chat.completions.create`、`tools` 和 `tool_choice=required` 实现模型工具调用协议。DeepSeek `deepseek-flash` 请求通过 `extra_body={"thinking":{"type":"disabled"}}` 关闭思考模式；供应商返回多工具调用时由执行器按上述单步协议纠正。SDK 仅负责 HTTP/API 协议；检索和调度自主实现。没有引入 LangGraph、ADK、AgentScope，原因是小范围场景无需其额外配置与依赖。

## 数据与安全

`data/documents.json` 中每条数据含 `id`、`version_date`、`version`、`scope`、`keywords`、`text`。D01、D02 故意包含没有适用范围判定依据的冲突。所有数据为合成内容。不在仓库写入或打印 API 密钥。

## 已知技术局限

1. 关键词检索覆盖有限（中文含混表达可能漏检）；上下文长度由用户提供。
2. 原文子串校验不是语义蕴含判断。即使引用准确，仍需检查答案是否受原文支持。
3. 结构化冲突校验仅覆盖本数据中标注的 claim key/value；真实文档需要更全面的冲突识别。
4. Chat Completions 模型供应商需支持函数调用；Responses-only 接口暂不支持。最小改造需要新增 Responses 适配器，将响应中的函数调用和 `function_call_output` 映射到现有 `model.next()` 契约，而不是只替换 base URL；在没有明确服务/模型配置时不猜测启用。
5. 实时模型行动不完全可控，虽已完成本次 7/7 验收，替换模型、提示或知识库后仍需重新运行验证。
