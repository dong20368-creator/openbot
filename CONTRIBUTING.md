# 贡献指南

从[架构](docs/ARCHITECTURE.md)定位模块，先用合成输入复现，再做必要修改。回答规则的执行入口见[规则说明](docs/POLICY.md)。

## 开发检查

建立环境后，在仓库根目录运行：

```powershell
python openbot.py test
python openbot.py demo
```

默认测试不调用模型、不发送微信。确需模型评估时，使用[手动评估入口](evaluations/README.md)，明确选择场景和调用授权。

## 提交内容

- 提交通用源码、合成测试和必要文档，保留公共路径适配。
- 不提交账号与群成员标识、凭据、真实聊天、媒体、数据库、日志或个人交接。
- 不扩大工具权限，不重放未知发送，不用历史数据库回滚代码。
- 涉及原生后端时，遵循该目录的上游开发规范和许可。

## 发布

逐项审查并按明确文件列表暂存；按暂存 Git blob 更新 `MANIFEST.json`，然后运行：

```powershell
python tools/check_release.py --check-anonymous-history --check-manifest
git diff --cached --check
```

提交作者与提交者沿用 `OpenBot contributors <contributors@openbot.invalid>`。检查不代替人工脱敏，不会自动推送；只有明确授权发布时才推送。首次单根发布才使用 `--require-anonymous-root`，后续更新保留历史。

真实部署和回滚按[维护指南](docs/OPERATIONS.md)执行，不将代码发布视为服务已升级。
