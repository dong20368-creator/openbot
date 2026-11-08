# 架构：沿着一条消息读代码

## 进程与数据位置

Windows运行Python助手，持有模型登录、队列和助手上下文。WSL运行资料读取器、原生发送网关以及独立的微信原生后端。两个位置都有名为runtime的目录，但内容不同，不能相互覆盖。

## 主流程

1. **启动与依赖检查**：`assistant/run_bot.py`取得单实例锁，`backend_preflight.py`核对WSL模块位置，`prepare_reply.export_inbox()`取得已核实的账号/群配置。
2. **接收与过滤**：`collector.py`持续收集，WSL的`inbox.py`只导出授权群、指定启用时间之后的提问。`db_reader.py`读取受限资料快照；昵称不是身份主键。
3. **入队与去重**：`core.Store`按稳定消息标识去重并持久化。同一成员的先后顺序与不同成员之间的公平调度分别约束，重启不靠内存猜状态。
4. **准备上下文**：`Store.history`组织本人近期对话与有限公开机器人答复；`Store.search_history`按相关性检索本人已确认旧问答，`dialogue_history.py`负责本人归档分页，`semantic_memory.py`提供本人作用域内的候选。`member_views.py`处理明确要求的群公开观点，不能访问别人的私有记忆。`media_context.py`读取已授权且绑定到本题的材料。
5. **生成**：`generation_pool.py`维护三路独立worker；`codex_client.py`负责模型通信、临时会话、白名单工具调用与复核。连接可复用，每题上下文仍独立。
6. **校验**：`evidence.py`检查来源读取凭据；`reply_contract.py`验证类别、材料状态、输出结构及长度。`answer_revision.py`支持有限的草稿局部修订，失败时保留原完整复核机制。
7. **发送**：主worker单路执行`native_sender.py`，通过WSL `native_gateway.py`和`protocol_transport.py`提交。提交前先持久化意图，提交后的未知结果仅查询原意图。
8. **确认后处理**：取得受支持的确认后才提交记忆。状态通过`runtime_health.py`写入去敏健康信息，由`control_panel.py`展示。

## 主要状态

| 状态 | 意义 | 运维动作 |
| --- | --- | --- |
| pending / retry | 等待生成或有界重试 | 查队列、生成worker和入站健康 |
| generating | 模型正在处理 | 查阶段耗时，不把慢直接当发送失败 |
| ready | 草稿已准备，等待发送 | 查发送worker及目标核验 |
| dispatching | 已进入发送阶段 | 不手工重复提交 |
| confirmed | 该意图已确认 | 不补发同一回答 |
| uncertain | 提交结果未知 | 只核对原回执，禁止新建意图重发 |
| failed / expired / cancelled | 未完成、过期或取消 | 结合原因判断，不能一律重发 |
| attention | 需要人工处理 | 先排查身份、范围或状态异常 |

具体转换及恢复门槛以`core.py`为准。这是阅读导航，不另建一套状态实现。

## 修改位置

| 目的 | 首先看 |
| --- | --- |
| 回答权限、表达、研究策略 | `answer_policy.py` |
| 数值预算 | `answer_limits.py` |
| 固定回复和输出结构 | `reply_contract.py` |
| 模型通信与工具分发 | `codex_client.py` |
| 用户上下文与任务顺序 | `core.py`、`context_budget.py` |
| 图片关联 | `inbox.py`、`media_context.py` |
| 公开资料适配 | `public_research.py`、`public_feed.py`、`public_pages.py` |
| 发送恢复 | `native_sender.py`、`protocol_transport.py` |

`desktop_io.py`、`desktop_sender.py`、`group_window.py`保留兼容/历史能力；桌面OCR发送不是当前主链路。公开版把图片OCR提取为`image_ocr.py`，媒体解析不再依赖相邻的旧项目或桌面发送器。它只识别传入图片，不抓取桌面。
