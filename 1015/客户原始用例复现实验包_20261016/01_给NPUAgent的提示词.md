# 直接交给本侧 NPU Agent

**结论：这轮实际执行复现，不再只分析；从客户原始server配置出发，跑真实MP格式17和同对象NPU/DIO对照，按真实错误收口。**

可直接发送下面这段，并把本文件夹中的脚本一并放入对侧测试容器。此前上下文继续有效。

```text
请执行本文件夹《00_执行说明.md》，不要只输出计划。

背景：客户fs_native+use_odirect=true，IsolatedLRU(0.8/0.1)，driven，chunk4096，L1上限512GiB；
镜像为nightly-releases-v0.25.1rc-anomaly_middleware-bugfix_0.2-lmcache-mp。
客户已手工应用PR305，接受这一前提，不再追安装/重装。
GPU同参数能通；本侧无GPU，优先NPU专属路径。
原始命令只起LMCache，客户DS的真实vLLM命令、KV格式和压缩group尚未核实。
此前Qwen正配额、普通K/V格式16小链路已有结果，不重跑；退出core旁支停止。

实际执行：
1. R0：collect_runtime.py记录已加载代码及真实SSD挂载。
   尽量用本侧已有客户原镜像；没有就用已有环境，并写清差异，不升级依赖。
   本轮只占自己的空闲NPU、目录和端口，不操作机主/他人服务。
2. R1：run_server.sh baseline + mp_probe_client.py，真实MP server提交一次格式17 STORE。
   保留chunk4096/fs_native/DIO/4worker/原IsolatedLRU；先L1=1GiB/init1GiB。
   使用新model/seq/key，核客户端ACK→server L2提交/成功/失败→同key文件/删除。
   inspect_store_log.py读取本轮server.log及cache目录；ACK不能直接当落盘成功。
3. R1b仅在写后删或清理干扰不明时：同样输入，仅移除L2 eviction，独立目录和端口。
   不重做正配额实验，不切成fs或buffered当修复。
4. R2：probe_npu_dio_chain.py，用同一真实Lazy对象：CPU前对照写读→
   真实Ascend格式17搬运→等待对应stream完成→逻辑内容核验→原对象再写读。
   记录B/o/p/q、L/P/view长度、余数、范围、native completion.error。
   默认真实adapter view；若旧B无padding而发生普通I/O回退，才做
   --padded-view-reference，并注明只是脚本局部复刻新版契约，未改生产adapter。
5. 实际O_DIRECT标志：有现成strace时在本次运行附带采集；没有写未核实，不安装工具。
6. R1c：小路径均通过且本侧资源允许时，恢复512GiB上限/20GiB初始注册跑一次同样小输入，
   独立目录及15567/18092，不填满池。资源不足写SKIP_RESOURCE，不强行执行。
7. 出现首个真实失败后按00第7节只补最小必要观测，别跑无限组合。
   若需要新增诊断行，只改测试脚本/专用副本，贴diff；不偷偷改生产代码使实验通过。

边界：小型格式17样例不是客户DS布局；小池不是512GiB验收；
实际DIO未trace不能冒称已核；Warm L1读回不是冷L2加载。
不通过bytes/clone替代I/O缓冲、不重复HostRegister、不制造早释放、
不查core、不准备GDB、不下载DS模型、不填满L1。
运行接口不兼容就报告精确缺失符号/签名，不拼凑静默降级。

超时：不代表server已停止访问IPC owner。若client报告cleanup_safe_to_release=false，
先只停止该轮server.pid对应且已核实身份的自建server；确认它退出后，
再touch本轮传入的server.stopped文件；不要先kill client。
R2若NPU同步失败，脚本保留内存并只重试同一stream同步，持续失败就保留错误/进程；
不能把MP server停机marker用于这个独立组件，也不reset设备。
保留独立client退出码，不把退出134抹掉，也不转去研究它。

回传用下面模板，只需文本。所有结论先给结果，再贴证据；不能确定写未核实。
```

## 统一文本回传模板

```text
【总结果】客户故障复现 / 仅复现配额清理 / 新NPU机制链通过 / 接口或环境阻塞
【首个失败】R编号、阶段、原始error；没有则写无，不推断客户已修复

【环境差异】
镜像tag/已有image ID：
LMCache/Ascend实际加载路径与版本：
native FS/Ascend .so路径：
相对客户：同镜像？同文件系统？实际DS布局？512GiB？（逐项已核/未核实）
容器base_path↔宿主路径、FS类型、f_bsize：
本次是否任何生产改动：无/具体diff

【R1真实MP】
实际server命令/运行目录/自建PID、端口：
实际格式枚举/plane/S/chunk：
model filename marker / seq / request id：
client STORE ack、设备完成、退出码、cleanup状态：
server L2提交/成功/失败原文（含task/adapter/key关联）：
本轮新文件名、数量、每文件字节、观察时间点：
同key删除完成/配额原文，没有则明确未观察到：
inspect分类与证据限制：
实际O_DIRECT系统调用标志：已核（原文）/未核实

【R1b】未触发/执行结果；若执行只改了什么，结果如何：
【R1c规模】已执行/资源不足；512GiB上限与20GiB初始注册下的同样小输入结果：

【R2同对象】
实际allocator、pool容量、A、D：
默认真实adapter view / 显式reference view：
同一源对象identity；B/o/p/q及各%D；L/P/view字节：
NPU实际派生搬运字节；slot/池范围检查：
CPU前对照store/load/内容结果：
NPU后逻辑内容、尾部、同地址、等待对应stream结果：
NPU后FS store/load/内容、原始completion.error：
结果是实际adapter机制通过/参考契约通过/普通I/O回退/失败：

【R3按信号补证】未触发/只新增了什么观测、实际结果、对应源码函数：
【仍未核实】
【建议修复边界】已有根因证据/尚无足够证据；若有，指出具体文件和最小改动意图
【保留现场】日志/JSON/文件路径；本轮自建server是否已停止；未碰他人服务
```
