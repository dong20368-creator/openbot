# 真实模型评估

这些脚本用于人工检查合成问题、上下文、公开研究和媒体理解，会调用你的模型账户及可选外部资料工具，产生额度消耗与本地结果。

```text
python tools/run_evaluation.py --list
python tools/run_evaluation.py evaluate_questions --allow-model-call
```

不使用生产微信发送器，不自动向群发布评估结果；不要自行把真实聊天历史加入测试。结果均保存在被Git忽略的runtime目录。evaluation_run.py记录代码指纹与场景标识，不复制认证配置。
