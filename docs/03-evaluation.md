# 03 · 验证结果（区分 Mock 与真实模型）

## 2026-10-28 已实际完成的测试

执行命令：

```bash
.venv\Scripts\python.exe -m unittest discover -s tests -v
.venv\Scripts\python.exe -m examples.generate_examples
.venv\Scripts\python.exe -m compileall -q src tests examples
```

结果：26/26 项 `unittest` 通过，`compileall` 通过，7 条 Mock 记录可重复生成。新增覆盖 DeepSeek 非思考请求、凭证脱敏、同轮多工具调用纠正、冲突完整取证，以及“多工具提议纠正后再观察真实搜索失败”的动态路径。环境为 Windows、Python 3.11.5、`openai==2.54.0`。

| 场景 | Mock 实际状态 | 对应 Mock 运行记录 | 实时模型验证 |
| --- | --- | --- | --- |
| 明确答案 | complete | `examples/offline_mock/01-normal.json` | complete，通过 |
| 文档冲突 | needs_clarification | `examples/offline_mock/02-conflict.json` | needs_clarification，通过 |
| 资料外 | not_found | `examples/offline_mock/03-out-of-scope.json` | not_found，通过 |
| 相似问法 | complete | `examples/offline_mock/04-paraphrase.json` | complete，通过 |
| 上下文追问 | complete | `examples/offline_mock/05-follow-up.json` | complete，通过 |
| 搜索故障一次 | complete（Mock 重试） | `examples/offline_mock/06-search-failure.json` | complete，通过；真实模型观察错误后重试 |
| 读取故障一次 | complete（Mock 重试） | `examples/offline_mock/07-read-failure.json` | complete，通过；真实模型观察错误后重试 |
| 模型步数上限 | incomplete | `tests/test_agent.py::test_step_limit` | 离线边界已测；实时未测 |
| 工具调用上限 | incomplete | `tests/test_agent.py::test_tool_limit` | 离线边界已测；实时未测 |
| 引用编造拦截 | 拦截 | `tests/test_agent.py::test_quote_guard` | 确定性校验 |
| 冲突强制回答拦截 | 拦截 | `tests/test_agent.py::test_conflict_guard` | 确定性校验 |
| 搜索前猜 ID 读取 | 拦截 | `tests/test_agent.py::test_read_requires_search_result` | 确定性校验 |
| 文档原文变化 | 回答和引用随之变化 | `tests/test_agent.py::test_answer_changes_when_document_text_changes` | Mock 回归，不是 LLM 证据 |
| 不同工具结果路径 | 成功搜索→读取；失败搜索→重试搜索 | `tests/test_agent.py::test_different_tool_results_change_next_action_in_mock_flow` | Mock 回归，不是 LLM 证据 |
| 模型请求/多工具协议异常 | 请求异常 incomplete；多工具提议回传纠正后继续 | `test_model_error_is_traced`、`test_parallel_calls_are_rejected_and_traced` | 确定性校验 |

> Mock 产生的不同路径仅说明执行器在模拟输入下的表现，不说明真实 LLM 能根据观察作动态决策。

## 真实模型验证：7/7 PASS

执行了：

```powershell
.venv\Scripts\python.exe -m examples.run_live_cases --all
.venv\Scripts\python.exe -m examples.live_evaluation_check
```

用户已在可读取 DeepSeek 环境变量的 PowerShell 中完成 7 个场景并定向复测早期失败项。最终 `python -m examples.live_evaluation_check` 输出 PASS，动态路径为 `successful search -> read_document; failed search -> search_documents`。

人工逐条检查结论：所有引用 ID 均存在，引用均为对应已读取文档原文子串；01、04、05、06、07 的事实由引用支持；02 同时引用 D01/D02，明确呈现 30 天与 15 天冲突，没有根据版本日期擅自确定优先级；03 两次检索为空后返回 `not_found`。06 第一次搜索返回 `ConnectionError`，下一模型回合主动再次选择 `search_documents`，之后读取 D04 并提交答案，构成真实的观察后调整证据。所有 Trace 步骤均包含参数、完整结果、摘要、下一动作和简短依据。

人工审查发现一项非阻塞措辞风险：03 回答中的“制度类知识库通常不收录”是对当前知识库类型的概括，不来自文档原文；它不包含公司事实，也不影响由两次零结果检索支持的 `not_found` 结论。真实 Trace 未被事后修改。

## 配置后必须完成的真实模型验证

```bash
python -m examples.run_live_cases --case 02-conflict --case 04-paraphrase --case 06-search-failure
python -m examples.live_evaluation_check
```

检查 `examples/live/index.json` 及每个 `.json`：

- [x] 运行 7 个场景，实际输入/输出/Trace 覆盖正常、缺资料、工具失败和全部必验点
- [x] 至少两条记录能从 **LLM 工具选择 → 工具结果 → 再次模型选择** 看出不同后续行为
- [x] 冲突场景中引用 D01、D02 的原文，未根据较新日期私自选择某一值
- [x] 相似问题检索到相关文档；历史追问能够定位原话题
- [x] 搜索与读取失败均被模型观察并选择重试，Trace 记录理由
- [x] 早期 `incomplete` 如实保留到定位阶段，修复后由运行器重新生成记录，未手工修改真实结果

实际验收模型：**DeepSeek 官方 `deepseek-flash`，Chat Completions 非思考模式**

最终七条记录报告 Token：**prompt 23,919，completion 2,624，total 26,543；其中 prompt cache hit 17,792、miss 6,127。该合计不包含被后续成功记录覆盖的早期失败尝试，因此不是账户账单总额。最终通过率 7/7。**

失败或偏差：**最终自动验收无失败；保留上述 03 概括性措辞风险和已知技术限制。**

## 判断标准与限制

实时运行日志应当来自实际 API 请求，`mode` 为 `live_llm`。`mode=offline_mock` 和 `mode=live_preflight_error` 都不应纳入真实模型效果统计。若模型服务不返回 usage，须注明计量缺失。由于关键词检索未采用向量搜索，可能无法检索词面差异很大的问法；当前引用校验不能自动排除所有断章取义。
