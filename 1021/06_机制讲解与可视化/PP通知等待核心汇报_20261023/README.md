# PP＋LMCache：等待机制与代表性效果

入口：[汇总HTML](<pp_lmcache_core_report.html>)。图、交互数据、原始观测字段摘录均内嵌，可单独复制HTML阅读；证据文件链接仍指向本地归档。

报告按“结论—等待机制—日志时间线—两组效果对照—PP1/PP2差异—汇报用语”展开。两条请求统一称为“正在计算的请求”和“缓存命中的请求”，源码术语作为括号注释。

只保留两组性能材料：19日PP2单并发完整命中的两对请求，用于解释计算节省与新增成本的收支；19/20日各形态四并发的共同成功配对，用于展示TTFT效果。234ms通知间隔来自21日另一组诊断，不与前组87.7ms直接相加，不视为相对PP1净新增或保证可删的时间。

生成入口：

```powershell
python -X utf8 -B E:\work\ks\output\ks_pp_lmcache_report_20261023\build_report.py
```

`report.template.html`保存展示内容，`report-data.json`保存所选数据、事件字段和证据路径。生成器同时写入本目录及现有可视化目录的`pp_lmcache_core_report.html`。

本地Chrome无界面模式已检查HTML渲染、7个时间点按钮生成与初始状态；首页及性能区截图已人工查看，记录见`display-verification.json`。未将初始化检查宣称为全部交互验收。

本轮仅整理已有材料，没有发送新模型请求、占卡、启停服务或改变推理源码。候选修改没有启用；KS-MEM-001保持OPEN。
