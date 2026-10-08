# KS实验附件

先阅读配套的《KS最少用例实验操作指南 零基础版》。本目录没有启动、停止或修改模型服务的脚本。

## 文件

- make_cases.py：Python标准库生成教学用JSONL；不能代替客户负载，不保证精确token数。
- run_manifest.csv：每轮配置和缓存状态。
- request_evidence.csv：M1及定位请求的证据链。
- stage_events.csv：需要补计时的事件格式，不是现成服务日志。
- comparison.csv：独立重复的原始值和对比指标。

## 生成示例

```bash
python make_cases.py --out-dir data/m1-r01 --run-id m1-r01 --count 3
python make_cases.py --out-dir data/m2-r01 --run-id m2-r01 --count 50
python make_cases.py --out-dir data/m3-r01 --run-id m3-r01 --count 50
```

正式扩样时生成全新的目录与run-id，并把count改为200。每一对开关必须用同一测量文件；不同用例和重复轮必须使用不同前缀，避免残留。脚本拒绝覆盖已存在目录。API与压测命令详见指南。

表中_ms为毫秒，_ns为纳秒，_seconds为秒，_fraction为0到1比例。空表没有实测结果；缺失字段填NA并说明原因。时间戳跨进程/节点时必须记录clock_domain，不能直接混减。P/D或多个PP rank的同一token不能重复相加。

本地验证仅覆盖文件生成、JSONL结构和拒绝覆盖，不证明任何NPU性能、模型正确性或现场复现。
