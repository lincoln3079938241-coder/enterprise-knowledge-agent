# 轻量企业知识问答 Agent（校招项目实践）

> **最终验证状态（2026-10-28）**：26/26 项标准库离线单测通过；DeepSeek 官方 `deepseek-flash` 七组真实模型验收 7/7 通过；自动检查输出 PASS。动态路径证据为“成功搜索 → `read_document`”与“失败搜索 → `search_documents`”。离线 Mock 与真实 LLM 记录仍严格分开。
>
> 全部制度和公司信息均为合成演示数据，不对应真实企业。此项目仅包含独立设计的实现，不转载试卷原文。

## 评审核心入口

| 关注点 | 路径 |
| --- | --- |
| LLM 工具选择/观察/循环和上限 | `src/agent.py` |
| 检索、读取、引用校验、冲突保护 | `src/tools.py` |
| 真实模型 Function Calling 适配 | `src/model.py` |
| 纯离线 Mock（非 LLM） | `src/mock_model.py` |
| 合成资料和版本日期 | `data/documents.json` |
| 自动化测试 | `tests/test_agent.py` |
| 离线模拟运行记录 | `examples/offline_mock/` |
| **真实模型运行记录（7/7 PASS）** | `examples/live/` |
| 测试和限制 | `docs/03-evaluation.md` |

## 环境

- Python >= 3.10
- 离线模式及单测无额外依赖
- 真实模型模式依赖 `openai` Python SDK，以及**支持 OpenAI Chat Completions `tools`/Function Calling 的模型服务**
- 不要求 UI、云部署、数据库或复杂框架

```bash
# 在项目根目录执行
.venv/Scripts/python.exe -m unittest discover -s tests -v
.venv/Scripts/python.exe -m examples.generate_examples
.venv/Scripts/python.exe -m src.main --mode offline --question "新员工入职之后怎么领取办公电脑？" --save examples/offline_mock/manual.json
```

本机已使用 Python 3.11.5 创建 `.venv` 并安装 `openai==2.54.0`；最终环境以 `requirements.txt` 的版本范围为准。

## 真实模型运行（必须由候选人操作）

安装依赖：

```bash
python -m pip install -r requirements.txt
```

macOS / Linux：

```bash
export OPENAI_API_KEY="你的模型API密钥"
export OPENAI_MODEL="该服务实际支持tool calling的模型名"
# 若使用兼容服务，才需要：export OPENAI_BASE_URL="https://服务商地址/v1"
python -m src.main --mode live --question "差旅报销最晚多久提交？" --save examples/live/02-conflict.json
```

Windows PowerShell：

```powershell
$env:OPENAI_API_KEY="你的模型API密钥"
$env:OPENAI_MODEL="该服务实际支持tool calling的模型名"
# 可选：$env:OPENAI_BASE_URL="https://服务商地址/v1"
python -m src.main --mode live --question "差旅报销最晚多久提交？" --save examples/live/02-conflict.json
```

DeepSeek 官方 API 使用以下非敏感配置：

```powershell
$env:OPENAI_MODEL="deepseek-flash"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
# OPENAI_API_KEY 仅在本地 PowerShell 中设置，不要写进源码、日志或提交记录。
```

`src/model.py` 在 `deepseek-flash` 下仍使用 `tool_choice="required"`，并自动附加 `extra_body={"thinking":{"type":"disabled"}}`。这是因为 DeepSeek 默认开启思考模式，而 required/named tool choice 只能在非思考模式使用。

运行三条关键路径（正常、冲突、搜索失败）并输出实际模型 Trace：

```bash
python -m examples.run_live_cases
```

运行七组完整验收场景（每个 case 需要多轮模型调用，可能产生 API 费用）：

```bash
python -m examples.run_live_cases --all
python -m examples.live_evaluation_check
```

只重跑失败场景、减少 API 消耗：

```bash
python -m examples.run_live_cases --case 02-conflict --case 04-paraphrase --case 06-search-failure
python -m examples.live_evaluation_check
```

**注意**：Codex 应用本身可以使用模型，**不等于**本 Python 脚本已获得 API key。某些服务或模型只支持 Responses API，而本版本调用 `/chat/completions`；需先确认所填服务和模型支持 Chat Completions 函数调用。OpenAI 官方的 [Function Calling 指南](https://developers.openai.com/api/docs/guides/function-calling)也明确区分了两类接口。不得将真实 key 发给他人、写入项目或 Git。

最终七条真实记录报告用量合计：prompt 23,919、completion 2,624、total 26,543，其中 prompt cache hit 17,792、miss 6,127。该数字只汇总最终保留的七条 Trace，不代表包含早期失败尝试在内的账户最终账单。

## 输入与输出

主要 CLI 参数：

- `--question`: 需要回答的问题
- `--history`: JSON 对话数组文件（格式见 `examples/history.json`）
- `--mode`: `live` / `offline`（默认离线）
- `--inject-failure`: `search_documents` 或 `read_document`，仅注入一次模拟失败
- `--max-steps`: 模型最大决策轮次（默认 9）
- `--max-tool-calls`: 工具最大调用次数（默认 7；搜索、读取和结构化提交均计数）
- `--save`: 将实际输入、结果、步骤和依据保存成 JSON

返回字段：`answer`、`citations`（精确原文）、`gaps`、`status`（`complete` / `needs_clarification` / `not_found` / `tool_error` / `incomplete`）、`trace`、`token_usage_reported`。

样例状态：

- 员工办公设备申领 -> 有依据的 `complete`
- 两份报销期限冲突 -> 引用两份原文的 `needs_clarification`
- 食堂菜单（库中无资料） -> `not_found`
- 搜索工具暂时失败 -> 模型可基于错误结果选择重试（实时模式必须实际验证）
- 达到调用上限 -> `incomplete`

## 设计特点与取舍

真正的 Agent 决策只由 `LiveChatModel.next()` 通过模型工具调用决定。执行器只提供工具、校验证据、实施预算上限、记录观察；不针对某种搜索结果写死下一动作。每次选工具后，完整工具返回被写回 `tool` 消息，接着**再次向模型请求动作**。模型通过 `submit_answer` 结构化完成，系统校验文档必须先由搜索返回、引用必须来自已成功读取的原文、冲突必须被暴露。Trace 同时保存实际参数、完整工具结果、摘要、下一动作和模型给出的简短依据，不保存隐藏思维过程。

离线模式的 `OfflineScriptedMock` 用于测试流程和边界；它根据工具结果采用规则，不证明模型驱动的动态决策，**不能充当真实 ReAct 实现**。

已知限制：检索为轻量关键词+同义词，未使用 Embedding；引用检查验证的是原文子串和冲突字段，不是全面的自然语言事实蕴含；不同模型可能不严格支持所有工具调用字段。模型同轮提出多个工具时，执行器不会预先执行，而是回传协议观察、要求模型在下一轮选择单个动作。Responses-only 服务需要在 `src/model.py` 增加协议适配后才能使用，不能仅替换 URL。人工审查还发现 03 的“制度类知识库通常不收录”属于无害的概括性措辞，并非文档原文或公司事实。

## 提交前检查

1. 以真实模型运行，验证至少两条因工具观察不同而改变动作的路径，并确保覆盖全部要求场景；检查每一条 `examples/live/*.json`。
2. 打开 `docs/03-evaluation.md`，填入真实模型测试结论、模型及调用耗费，并删除/保留预检错误记录以反映最终真实状态。**不可只提交离线 Mock 记录作为真实决策证据**。
3. 阅读 `docs/04-ai-usage.md`，准确补充你使用的 Codex、ChatGPT、自己编写/修改、复现及实际投入时间；不要填写未经验证的声明。
4. 提交前检查没有真实密钥、日志隐私资料，设置 GitHub 仓库可供评审核验，保留最终 SHA。

```bash
git init
git add .
git commit -m "Implement evidence-grounded document QA agent"
git rev-parse HEAD
# 创建自己的 GitHub 仓库后，再添加 remote 并推送
```

## 文档

`docs/01-requirements.md`、`02-solution.md`、`03-evaluation.md`、`04-ai-usage.md`、`05-retrospective.md`。
