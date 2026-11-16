# OpenBot

基于 **Windows + WSL** 的微信群问答助手，支持连续对话、图片与文档理解、公开资料研究和按成员隔离的记忆。

当前为开发者源码版，需要自行配置微信后端、账号及模型登录。仓库不提供账号、凭据或聊天数据，不是微信官方项目。

## 快速体验

先按[快速开始](docs/QUICKSTART.md)安装 Python 3.12 和依赖，再在仓库根目录运行离线演示，无需连接微信或登录模型：

```powershell
./assistant/.venv/Scripts/python.exe openbot.py demo
```

接入真实服务，继续阅读[部署指南](docs/DEPLOYMENT.md)。

## 功能

- **连续对话**：承接追问、纠正与群内公开话题，按成员隔离个人记忆。
- **材料理解**：处理授权图片和文档，区分已读内容与缺失材料。
- **公开研究**：查询公开信息、核验来源，支持可选只读行情与计算工具。
- **可靠发送**：三路生成、单路发送；持久化发送意图，未知结果先核对回执。
- **显式模型选择**：支持 Codex 与 Antigravity 订阅，不自动切换计费渠道。

## 阅读入口

| 目的 | 文档 |
| --- | --- |
| 配置运行环境和模型 | [部署](docs/DEPLOYMENT.md) · [配置](docs/CONFIGURATION.md) · [模型接入](docs/PROVIDERS.md) |
| 理解或修改代码 | [架构](docs/ARCHITECTURE.md) · [回答规则](docs/POLICY.md) · [贡献指南](CONTRIBUTING.md) |
| 运维与验收 | [维护](docs/OPERATIONS.md) · [验证范围](docs/VALIDATION.md) · [隐私边界](docs/SECURITY.md) |

完整索引见[文档导航](docs/README.md)。

## 目录

```text
openbot.py       演示、测试、初始化与准备状态检查
assistant/      助手源码及 Windows 启停入口
docs/           使用、部署、架构与维护文档
examples/       无秘密配置示例
tests/          默认隔离测试及辅助用例
evaluations/    手动运行的真实模型评估
tools/          测试与发布检查
native-backend/  原生微信后端及上游许可
```

## 使用边界

群聊不能修改系统权限；授权上下文可能发送给云模型。来源核验不等于逐句事实审计，项目不保证秒回或永久在线。详见[隐私与安全](docs/SECURITY.md)。

## 许可

原生后端基于 [thisnick/agent-wechat](https://github.com/thisnick/agent-wechat)，保留上游 MIT 许可。原创助手源码尚未额外授予统一开源许可；公开可见不等于任意再分发授权。见 [NOTICE](NOTICE.md)。
