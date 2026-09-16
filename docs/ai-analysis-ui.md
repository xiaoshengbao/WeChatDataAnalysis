# AI 按需分析界面

已接入当前生产 DeepAgents 的主助手。普通回答沿用文字流程；模型取得资料后，可以调用 `create_analysis_ui`，决定是否展示、选择组件和组合方式。显式纯文字要求优先，普通问答和单值统计无需界面，也没有额外的“是否画图”模型调用。

## 实现

- 前端使用 `@json-render/core` / `@json-render/vue` 0.20.0 的 `defineCatalog`、`defineRegistry`、`JSONUIProvider` 和 `Renderer`；图表使用 ECharts 6.1.0 / vue-echarts 8.3.0。
- 注册 Stack、Grid、MetricCard、DataTable、Chart、SourceList。Chart 支持折线、柱状、饼图、热力图。主题、字体、间距统一控制，窄容器转单列，图表可展开查看。
- 表格筛选、排序、分页，图表图例和缩放，均只操作快照，不调用模型或发起新查询。来源按钮复用原消息定位。
- 模型仅提交组件 JSON 和字段绑定。后端、前端分别校验组件白名单；不开放 json-render actions、表达式、动态绑定，也不执行模型生成的 JavaScript、HTML、URL 或网络请求。
- UI 与 ECharts 按需加载；旧文字消息继续正常展示。

官方接口参考：[json-render](https://github.com/vercel-labs/json-render)、[Vue 渲染器](https://github.com/vercel-labs/json-render/tree/main/packages/vue)、[vue-echarts](https://github.com/ecomfe/vue-echarts)。

## 工具与数据绑定

工具参数：`title`、`spec`、`scope_handle`、`calculation_id`、`reuse_ui_id`。`spec` 为 `{root, elements}`，每个元素包含 `type`、`props`、`children`；后端为省略的 props 填充默认值。成功返回 `ui_id` 和正文引用 `[[ui:编号]]`，引用应单独占一行。

| 数据集 | 字段 | 来源 |
| --- | --- | --- |
| totals | total_messages, active_senders | 已执行 count_messages 的保存范围 |
| daily_totals | day, count | 程序按范围时区分日统计 |
| sender_ranking | sender_id, sender, count | 程序按成员标识聚合 |
| by_day_sender | day, sender_id, sender, count | 按日和成员聚合 |
| findings | text, event_time, evidence_status | 已保存且来源在当前范围内的发现 |
| calculation | value, event_count, operation | calculate_values 返回的 calculation_id |
| calculation_terms | event_key, value | 已确认计算项，附来源 |
| sources | sender, text | 最多 100 条来源示例，附原消息定位依据 |

MetricCard 使用 `dataset/field`；DataTable 使用 `dataset/columns`；Chart 使用 `dataset/chart_type/x/y`，热力图另填 `value`，多系列可填 `series`；SourceList 绑定 `sources`。成员维度优先使用 `sender_id`，程序显示名称与标识，防止同名成员合并。

统计分页由程序继续读取已保存结果，不将第一页当完整分布。完整、无警告的每日统计补齐范围内零消息日期；有读取缺口时保留已读日期并说明缺失日期不代表零。超过 10000 天的宽范围可收缩到首末消息日期并注明其余日期为零；仍超限则返回可修正错误。资料发现和计算结果明确标注覆盖限制。

保存范围、开始/结束时间（右端不含）、时区偏移、成员条件、统计口径、完整性、来源及数据快照。计数按消息记录，发言人数按成员标识去重；缺少标识时只能按会话与昵称区分，这一限制会在界面口径中说明。

## 消息、恢复与复用

1. 工具校验后保存 `analysis_ui` piece，并向运行快照添加可选 `ui_artifacts`，发送 `analysis_ui` SSE。
2. 模型在回答中插入引用。`AgentAnswer` 将 Markdown 与 Vue 界面按顺序展示；人物、图片和消息来源引用保持兼容。
3. 未完成的引用在流式阶段隐藏；完整引用等待对应的校验后快照。稳定 Vue key 保留追加文字时的图表、筛选状态。
4. 最终 SSE、历史消息和持久化运行保留快照。刷新/重启从保存结果恢复，无需重跑模型。渲染出错则展示文本摘要和数据表。
5. 复制回答会展开指标、表格与覆盖说明，不复制 UI 内部协议；代码示例中的字面引用不替换。
6. 后续修改传 `reuse_ui_id`。后端仅允许同账号、同对话的已保存界面；读取不可变快照生成新 ID，保留 `derived_from`，历史回答不变。新轮次提示只携带最近 8 个界面的轻量元数据，不重新注入整份数据。

工具仅在当前版本主助手已具备统计、计算、发现或历史快照时暴露；子助手和旧检查点图不新增工具。错误返回给模型修正参数，不要求重写整篇回答。

限制：每个界面最多 40 节点、6 层、每数据集 10000 行、总快照 2 MiB；折线/柱状最多 32 系列、20000 个展开坐标；饼图最多 100 项，不接受负数或多系列；图表数值须有限且在 JavaScript 安全整数范围内。暂不提供任意代码、界面内查询、独立仪表盘或通用表单。

## 验证

本次后端定向回归 167 项通过；前端 Vitest 28 个文件、394 项通过，`npm test` 中的 Node 测试及生产构建亦通过。构建仍有既有重复导出/CSS 提示和依赖注释提示，不阻塞产物生成。

后端协议、权限、分页、时区、零日期/缺口、数据绑定、SSE、历史恢复和 DeepAgents 集成测试：

```sh
.venv/bin/python -m pytest tests/test_ai_analysis_ui.py tests/test_ai_deep*.py tests/test_ai_agent_sse.py -q
```

前端渲染、复制、流式片段、稳定组件、本地交互、错误回退和原有聊天回归：

```sh
cd frontend
npm test
npm run build
```

浏览器样例（含实模生成的脱敏合成数据界面）：

```sh
cd frontend
npx vite --config vitest.config.js --host 127.0.0.1 --port 5179
# 打开 http://127.0.0.1:5179/tests/fixtures/analysis-ui.html
```

已人工核验实际 SVG 图表、窄侧栏、暗色主题、展开图表、过滤后追加文字保持状态，以及纯文字切换。单元测试覆盖四类图表配置。未将浏览器样例等同于桌面安装包发布验证。

固定问题集可通过已有模型配置运行；仅使用合成聊天，不读取真实微信内容。会正常消耗所选模型的调用额度，模型密钥仅在内存中使用，结果写入独立测试目录。

```sh
PYTHONPATH=src .venv/bin/python tools/verify_analysis_ui.py \
  --data <应用数据目录> --profile <模型配置ID> \
  --output <独立测试目录> --timeout 120
# 可选 --cases dashboard,revise
```

2026-09-16 使用已配置 deepseek-flash 的观察记录（端到端耗时，非性能承诺）：

| 问题 | 模型调用 | 耗时 | 观察 |
| --- | ---: | ---: | --- |
| 问候 | 1 | 2.85 s | 无工具、无界面 |
| 单值统计 | 3 | 2.94 s | 查询及统计，纯文字 |
| 显式不要图表 | 3 | 6.77 s | 纯文字 |
| 自主选择每日趋势表达 | 6 | 18.77 s | 自主选择图表、指标、表格 |
| 组合展示（独立复测） | 4 | 7.92 s | 指标、柱状图、成员表，程序总数为 6 |
| 改折线（紧接复测） | 2 | 2.74 s | 仅调用 create_analysis_ui，复用快照，无重新查询 |

第一轮组合问题曾重复调用 select_chat_scope，21 次模型调用后未完成；紧接的修改题也因此未满足“复用先前界面”检查。单独复测组合与修改均通过。该记录说明能力链路可用，但模型是否选择可视化、能否稳定完成工具调度仍取决于模型，不宣称所有模型或每次运行都会作出同样选择。

实现入口：后端 `ai/analysis_ui.py`、`deep_tools.py`、`deep_runtime.py`；前端 `AgentAnalysisUI.vue`、`AnalysisChart.vue`、`analysis-ui-catalog.js`、`agentMarkdown.js`。
