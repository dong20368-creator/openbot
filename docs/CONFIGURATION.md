# 配置与私有数据

[文档导航](README.md)

模型渠道配置与官方客户端路径见[模型接入](PROVIDERS.md)。未配置时仍使用 Codex；运行配置不随源码同步。

所有真实运行资料都留在被Git忽略的runtime目录。模板只说明结构，不能直接连接到任何账号。

## Windows：assistant/runtime

| 路径 | 用途 | 初始化方式 |
| --- | --- | --- |
| model-provider.json | 显式选择模型渠道；缺省Codex | 按[模型接入](PROVIDERS.md)创建 |
| activation.json | 消息启用时间 | `openbot.py init`，写入当时Unix时间 |
| media-consent.json | 是否允许向云模型提交引用媒体 | 初始false，操作者明确同意后再修改 |
| codex-home/ | 专用模型配置和登录 | 通过`login-codex.cmd`使用自己的账号登录 |
| state.sqlite3 | 队列、上下文和记忆 | 程序运行创建，不能用旧库回滚代码 |
| models/embedding/ | 本地语义模型 | 按`semantic_memory.py`指定模型预下载 |
| STOP / bot-status.json | 正常暂停和健康状态 | 由面板/运行程序管理 |

`model_provider.py`负责渠道选择，推理档位保持medium。未配置时使用Codex订阅；其他渠道必须显式选择。型号配置不代表账号具有调用权限，失败不自动回退到另一渠道。

`WECHAT_CODEX_BINARY`可指定可执行文件。公开版登录和模型子进程共用同一查找函数：优先此变量，其次PATH中的codex；不再依赖某台电脑固定的Node安装目录。

## WSL：/opt/wechat-agent-v2/runtime

| 路径 | 必要字段/作用 |
| --- | --- |
| account.json | account为真实机器人账号ID，group为唯一授权群ID；不能用昵称替代 |
| transport.json | mode=native、endpoint、native_account、server_id、token_file、profile_home、keys_file |
| text-mention.json | 可选enabled_since：启用纯文字提及兼容的起始Unix时间 |
| auto-image.json | 可选enabled_since：启用自动关联图片的起始时间 |
| native-delivery.sqlite | 原生提交意图与回执，保留完整性 |

`native_account`是后端内部实例ID，`account`是微信账号ID，两者不是一个概念。`profile_home`指向包含xwechat_files的该实例资料目录，`keys_file`指向后端生成的profile-keys.json。不得把这些文件上传。

原生endpoint仅允许loopback。上游默认端口6174；模板使用6175作为适配示例，必须和你的实际服务一致。server_id和账号/群都要从自己的后端核实，不猜测。

## 固定部署约定

当前Windows桥接代码默认WSL发行版`WeChatAgent`、后端路径`/opt/wechat-agent-v2`、容器名`wechat-agent-v2-protocol`。这些是当前实现约定，不是已自动创建的资源。改变布局需同步检查所有调用位置；本次发布没有把部署逻辑重写成跨平台安装器。
