# 文档导航

[项目首页](../README.md)

## 体验与部署

按顺序阅读；只想看离线流程时，完成快速开始即可。

| 顺序 | 文档 | 内容 |
| --- | --- | --- |
| 1 | [快速开始](QUICKSTART.md) | 环境、演示、隔离测试与空配置 |
| 2 | [部署指南](DEPLOYMENT.md) | Windows、WSL 和微信后端接入 |
| 3 | [配置参考](CONFIGURATION.md) | 文件位置、必要字段和部署约定 |
| 4 | [模型接入](PROVIDERS.md) | 显式渠道、登录要求与客户端路径 |

配置样例见 [examples](../examples/README.md)。

## 开发与维护

| 文档 | 内容 |
| --- | --- |
| [架构](ARCHITECTURE.md) | 一条消息的处理流程、状态和修改位置 |
| [回答规则](POLICY.md) | 提示规则、工具权限与输出校验 |
| [贡献指南](../CONTRIBUTING.md) | 修改、测试、脱敏与发布流程 |
| [维护](OPERATIONS.md) | 启停、排错、升级和回滚 |
| [隐私与安全](SECURITY.md) | 数据流、外发范围与发布边界 |
| [验证范围](VALIDATION.md) | 当前检查、验收限制与历史记录 |

隔离测试见 [tests](../tests/README.md)；会调用模型的手动评估见 [evaluations](../evaluations/README.md)。
