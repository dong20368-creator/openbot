# 配置示例

这些文件只有示例字段，不含可用账号或凭据。按自己的环境核实后配置，不要直接启动服务。

| 文件 | 目标位置 | 用途 |
| --- | --- | --- |
| [activation.example.json](activation.example.json) | Windows `assistant/runtime/activation.json` | 消息启用时间，推荐由 `openbot.py init` 创建 |
| [media-consent.example.json](media-consent.example.json) | Windows `assistant/runtime/media-consent.json` | 媒体外发授权，默认关闭 |
| [account.example.json](account.example.json) | WSL `/opt/wechat-agent-v2/runtime/account.json` | 自行核实的账号与授权群 |
| [transport.example.json](transport.example.json) | WSL `/opt/wechat-agent-v2/runtime/transport.json` | 原生后端与本地凭据位置 |

模型选择示例见[模型接入](../docs/PROVIDERS.md)，字段及两个 runtime 目录的区别见[配置参考](../docs/CONFIGURATION.md)。实际配置必须保持本地，不提交到 Git。
