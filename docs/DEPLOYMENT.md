# 部署到自己的机器

这份源码把Windows助手与WSL原生微信后端连接起来。真实登录、群授权、外发媒体和定时任务都由部署者明确决定。以下按检查点组织，不声称是新机器一键安装。

## 1. 先完成离线验证

按[快速开始](QUICKSTART.md)建立Windows Python环境，运行`demo`和`test`，用`init`准备空目录。不要复制别人的runtime、auth.json或资料库。

## 2. 准备原生后端

准备名为`WeChatAgent`的WSL2 Linux环境和Docker。在`native-backend/`中读取`README.md`及`deploy/native/README.md`，按上游锁定工具链构建本仓库版本，例如：

```sh
docker build -f deploy/native/Dockerfile -t openbot-native:local .
```

当前助手的启动脚本期望容器名`wechat-agent-v2-protocol`。根据上游compose示例配置自己的token文件、资料挂载和loopback端口，保持只读根目录、最小权限及单资料写入者。不直接采用不含本地修订的旧镜像来代替本仓库源码。

通过上游`wx`CLI创建自己的实例并完成登录；上游文档提供`accounts create`、`auth login`与服务端配置流程。不要运行针对其他资料目录的接管/迁移命令。此发布不包含现成登录状态。

## 3. 安装WSL Python桥接

将`assistant/`中的Python模块复制到`/opt/wechat-agent-v2`。WSL读取与媒体处理所需依赖包括Python、cryptography及媒体/文档处理包；Windows完整锁定文件含Windows专用包，不可整份用于Linux。

配置Linux文件权限，确保只有预期运行用户可读runtime。按[配置说明](CONFIGURATION.md)创建account.json和transport.json，并使用后端实际返回的实例/账号/服务/群身份及资料路径。

可先在WSL中执行：

```sh
cd /opt/wechat-agent-v2
python3 -c "import inbox, media_reader, member_window, group_summary, native_gateway, native_connection_health; print('imports ok')"
```

这只证明导入成功，不证明账号已登录、群正确或发送可用。

## 4. 准备Windows模型环境

安装自己的Codex CLI，运行`assistant/login-codex.cmd`完成专用环境登录。不要把登录文件提交到Git。需要富途工具时在该专用环境中自行配置；不附带任何现成OAuth或账号数据。

语义检索使用`semantic_memory.py`声明的BAAI/bge-small-zh-v1.5模型。该代码只读本地缓存，部署者需要先下载对应模型到assistant/runtime/models/embedding。模型未准备时，不要宣称记忆检索已经可用。

## 5. 核对，再启用

确认Windows没有运行其他worker、WSL后端在线、资料只有一个写入者。然后从assistant目录执行：

```powershell
./.venv/Scripts/python.exe run_bot.py --check
./start-bot.ps1
./control-panel.cmd
```

`--check`会读取自己的后端并核对目标；它使用与worker相同的独占锁，不能和运行中的worker并行执行。检查成功仍不是微信群实收验收。首次群内测试应由操作者在明确授权的目的群完成。

## 6. 可选功能

媒体外发初始关闭；操作者明确同意后再设置media-consent。文字提及和自动附图分别依赖WSL启用时间配置。定时摘要没有随源码自动创建计划任务；需独立配置并确认时间、范围和暂停行为。

停止、回滚、未知发送排查见[维护](OPERATIONS.md)。
