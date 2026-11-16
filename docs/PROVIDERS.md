# 模型接入

[文档导航](README.md)

在 `assistant/runtime/model-provider.json` 显式选择渠道。缺省仍为 Codex；调用失败不会自动切换模型或计费渠道。不要提交本地配置、登录缓存或密钥。

```json
{"provider":"antigravity","model":"gemini-4-argon-medium"}
```

Antigravity 使用自行安装的官方 `agy` ACP 客户端及已有登录。通过 `OPENBOT_ANTIGRAVITY_BINARY` 指定可执行文件，或放在 PATH / `assistant/runtime/antigravity/agy.exe`。仓库不提供客户端二进制或订阅。账号须实际拥有对应模型资格；支持范围取决于客户端和账号，菜单显示不等于可调用。

每次请求建立独立 ACP 会话，保留完整规则及 medium 档位。原生工具只用于公开搜索；受限主机工具继续经过原作用域分派、输出和来源检查。结束时清理请求目录，不复制或删除共享登录，不回退到 API key。

公开网页读取仅允许 HTTP(S) 公网目的地，逐跳核对重定向并固定已验证 IP。DNS 返回 Fake-IP 网段时向 Cloudflare DoH 查询公网地址。支持 HTML、文本、JSON，不保证登录页面、动态页面或网页 PDF 可读。成功正文只在同一请求内缓存，结束清空。

富途仍使用 Codex 只读 MCP 宿主，需要独立配置 Codex 登录与 MCP。启动预热不进行模型推理或加载聊天。工具不可用时不能编造行情。

其他显式配置：

```json
{"provider":"codex","model":"gpt-6-astra"}
```

保留 32 轮默认上下文、完整历史检索、来源核验及 medium 推理。新增整批工具参数校验、一次格式修复、同请求网页去重和去敏阶段耗时。修复同时间戳遗漏已发送公开答复的问题，同时排除当前及未来任务，减少错误补查历史。复杂研究仍可能较慢，不保证秒回。

模型身份等固定问题的本地直接回复尚未实现，目前仍经过模型流程。
