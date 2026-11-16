# 助手源码

从仓库根目录使用 `openbot.py` 运行演示、测试、初始化和准备状态检查。

- 运行入口：`run_bot.py`；Windows 启动：`start-bot.ps1`。
- 主流程与模块对应：[架构](../docs/ARCHITECTURE.md)。
- 模型选择与客户端：[模型接入](../docs/PROVIDERS.md)。
- 回答策略及工作规范：[规则说明](../docs/POLICY.md)。
- 私有配置与数据位置：[配置参考](../docs/CONFIGURATION.md)。

代码保留当前模块布局，避免改动 Windows/WSL 导入和部署路径。`runtime/` 是本地运行资料，不属于公开源码。
