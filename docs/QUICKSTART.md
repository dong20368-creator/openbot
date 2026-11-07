# 快速开始

先验证离线流程，再考虑真实部署。以下命令从仓库根目录执行。

## 1. 建立环境

当前助手以 Windows + Python 3.12 为主要验证环境。完整依赖包含Windows专用包，不要直接在Linux安装同一份锁定清单。

```powershell
py -3.12 -m venv assistant/.venv
./assistant/.venv/Scripts/python.exe -m pip install -r assistant/requirements-lock.txt
```

锁定文件是原开发环境快照，依赖源的未来可用性及全新机器安装仍需实际验证。

## 2. 看一个无网络演示

```powershell
./assistant/.venv/Scripts/python.exe openbot.py demo
```

演示使用临时SQLite数据库、虚构账号、假模型和假发送器，让你看到消息入队、生成、确认以及重复输入被拒绝。它不读取真实聊天、不连接微信、不调用模型。

## 3. 运行隔离测试

```powershell
./assistant/.venv/Scripts/python.exe openbot.py test
```

测试运行器为测试进程准备独立临时目录。默认选择与现行主链路相关的回归；不会把真实模型评估混进测试。查看具体选择见 `tools/run_tests.py`。

## 4. 创建空配置骨架

```powershell
./assistant/.venv/Scripts/python.exe openbot.py init
./assistant/.venv/Scripts/python.exe openbot.py check
```

`init`只创建缺少的本地文件，保留已有文件，不登录、不启动服务，也不授予图片外发权限。初次`check`显示尚未准备完成是正常的；它只检查本地准备状态，不验证WSL在线、群身份或模型真实请求。

接下来按[部署说明](DEPLOYMENT.md)配置。不要直接运行目录中不理解的旧桌面辅助代码。
