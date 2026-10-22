# 可视化报告的数据口径与入门解释

本文件配合[report-data.json](E:/work/ks/output/ks_pp_lmcache_explained_20261021/report-data.json)。仅重排已有证据，没有新增请求、占卡、启停服务或修改运行源码。不包含聊天原图。

## 建议先讲明白的结论

历史各自ON/OFF表明：4K四并发全命中时，PP1这组平均TTFT下降43.06%，PP2这组下降6.78%。**差异不只有“同样的新增开销占比变大”：两套布局原先的冷态Attention执行成本就不同，命中能删除的时间本来就不同。**现有设备与安装源码把这个差异缩小到query/heads分工、KV准备的重复与复用，以及小矩阵执行效率。

与此同时，两套配置同时改变了TP、CP、P节点和观测条件，因此报告应说“当前PP2/CP=true布局与PP1/CP=false布局”，不能说已经证明PP个数单独造成全部差异。命中后的通知耦合、固定恢复任务和末token软件链还会压缩剩余收益，它们要与冷态Attention差异分开讲。

## 四列正式结果怎么画

使用`historical_c4_matrix.columns`作为横轴，`metrics`作为纵轴，固定顺序为：PP2关闭、PP2开启、PP1关闭、PP1开启。中文label、单位和源路径均已放在JSON中。

核心均值只使用**每种形态各自的27对同输入且共同成功请求**。并非跨四列建立了同一个27请求集合。两组都是4096输入、256输出、最大并发4、每侧正式32条；ON的32条都查到完整外部缓存。PP2 OFF/ON各成功30/29条，PP1 OFF/ON各成功27/32条。

最重要的读图事实是：

- PP2 TTFT为1191.828→1111.015ms，PP1为2096.525→1193.737ms。两组ON结果比较接近；**OFF的差距大得多**，所以要解释为什么原先计算成本不同，不能只盯ON额外等了多久。
- PP2端到端7.498→7.537秒，PP1为8.300→7.565秒。TTFT改善不保证整个请求同比改善；输出与MTP接受轨迹也可能变化，不能把首token后差值直接称为D算子性能变化。
- 这是各一轮的顺序实测，失败还会改变闭环发压队列。共同成功过滤避免把失败零值算成加速，不等于消除了所有干扰。

**Prefill和Decode的空值要保留。**`pure_prefill_ms`、`d_decode_ms`写null，是因为缺少这27对请求相同边界的纯执行区间，不能拿TTFT或推导数冒充。`decode_s`另标“首token后客户端耗时”，它由已匹配的客户端真实结束时间与首token时间计算，既不等于D纯计算，也不是用TTFT和TPOT拼出来的估计值。

`supplement_all_formal_p_windows`可以作为独立小表：PP2 OFF/ON约554.916/504.216ms，PP1 OFF/ON约1532.917/623.631ms。**它们各包含全32条P侧正式窗口，可能含后来D失败的请求**，不可直接放入27对延迟矩阵叫“平均Prefill”。P窗口还包含缓存恢复、调度、模型/MTP/采样和结果消费；四并发不同请求可能共享物理批次，不能相加当总执行时间。

具体证据指针：`sources.paired`对应的`/groups/0/tiers/1`为PP2、`/groups/1/tiers/1`为PP1；每个metric的`sources`保留官方JSON、client JSONL与字段名。输入配对及唯一时间关联的既有审计在`/verification`。这次没有重算整套原始结果。

## “少算的百分比”为何差这么大

先把几个名词拆开：**query**可以先理解为“这次要为哪个输入token计算注意力”；**heads**是同一token并行进行的多组注意力计算。这套MLA/SFA的多个query heads共享KV，不能理解成64heads就要从LMCache各搬64份KV。

当前源码对应的两种冷态分工是：

1. **PP1、TP16、CP=false**：一个query的64heads拆到16卡，每卡算4heads。每张卡都要为它负责的4heads查稀疏索引、收集离散KV并反量化；同一query的准备动作因heads分卡而在多卡重复。
2. **PP2、TP8、CP=true**：query沿token维度分给8卡，每卡保留64heads。该卡为自己的query准备一份KV后，让更多heads共用它。物理query从8192降为1024，并不表示整个模型只处理了1024输入；其他query由其他CP卡负责。

这意味着**整层理论注意力算术配对数可以相同，执行成本却很不同**。现有CPU工作模型中，PP1全部16卡与PP2同一层全部8个CP卡，`query×heads×selected_KV`配对总数都为939,589,632。它是源码及有效长度假设下的工作量，不是硬件实测FLOPs。真正改变的是准备工作重复多少、多少heads共用一次准备，以及矩阵任务是否够饱满。

安装native源码还有一个具体分支：4heads会先按16行对齐；完整128列tile下，MMAD后满足小矩阵条件并进入`PIPE_M`同步。64heads的完整tile不满足该条件。**这不是PP2 ON新添的慢动作；它主要解释PP1冷态为何有更多低效率计算可以被命中省掉。**同步不能直接删除，其正确性和耗时份额都没有独立验收。

今日设备观察与上述解释相容：PP1冷8K主模型SFA每层约21.108ms，PP2前段末CP片约3.143ms。最重核组工作模型预测7.95倍，实测6.72倍，残差−15.56%，不靠拟合参数掩盖。模型能说明方向和数量级，但剩余部分不能随意分配给某一条指令。

**热态也要分rank：**PP1主模型每层约0.0875ms；PP2首CP片约0.0953ms，而末CP片约0.0160ms。它们的有效query负载不同，不该把所有PP2 rank都统一写成0.09ms。PP1 MTP末两次仍传8192×4的物理buffer，SFA却只有约0.086ms，这个反例已经证明“物理shape长”不等于“有效query都重算”。

## 两条样本的收支图不能冒充整档

`historical_two_sample_action_budget`专门保存历史真正ON/OFF的两条全命中P侧观测：

- PP2计算批窗口447.870→369.309ms，少78.561ms（17.54%），新增恢复与推进87.733ms，最终P窗口增加约9.172ms。
- PP1计算批窗口1222.667→446.835ms，少775.833ms（63.45%），新增恢复与推进122.228ms，最终P窗口减少约653.604ms。

这里“计算批窗口”是主机观察到的计算批链路，包含软件和异步执行推进，不是纯数学kernel时间。每形态仅两条，不能画成全C4正式平均，也不能与今日SFA设备总和直接相减。此处适合展示“原本可删除的一块为何不同”，不适合宣称已经量出每个子环节的纯因果份额。

## 恢复环节：为何少搬一半，软件动作没少一半

`kv_restore_observations.requests`来自真实PP1两条、PP2十二条热请求，共224份READ，逐项有来源及JSON pointer。PP2与PP1同长度的对比只取都带CANN的单并发样本，不能夹入fixture或四并发请求混算：

- 4K计划payload：PP1为4503MiB，PP2为2251.5MiB；两者仍各16份READ、32次native提交尝试、128个对象/索引staging copy。
- 8K计划payload：PP1为9006MiB，PP2为4503MiB；两者仍各16份READ、64次native提交尝试、256个对象/copy。
- 4K handler主机占用合计84.241→73.561ms，8K为99.814→77.102ms，只减少约12.7%/22.8%。计划字节减半，固定的RPC、索引、event和提交任务没有减半。

READ内部仍要取得对象、导入生产者event、建立stream依赖、准备block索引、提交native、记录完成event、安排对象释放/归还、导出event。这里`source_pinned`只对应256B或512B的小block-ID索引，不表示几GiB的KV payload都被pin。

native记录的是**计划/尝试**的字节及launch，不能当设备已完成的DMA字节、时间或带宽。handler结束也不保证设备已经搬完。四并发每请求自己的handler占用约51–90ms，第一份进入至最后一份退出约153–160ms，中间包含别的请求和处理间隙；它们不能叠加称同一个请求的纯服务时间。

这批热请求各rank只有一份READ，native把同一段拆成2次或4次提交，不是重复全量恢复2遍或4遍。未观察到重复全量READ，不等于已证明每一字节DMA绝无重复。

## 今日22条与历史正式benchmark的界线

今日22条由PP1带CANN6条、PP2带CANN6条、PP2无CANN热态控制6条、PP2追加C4诊断4条组成；均完成256输出。带CANN的每种形态有两条预热、四条有效冷/热诊断。**它们全部使用LMCache ON，今天没有新的OFF。**

追加C4是同一个已缓存4K输入同时发4条，用于抓动作依赖；不是原32条C4正式用例。今日图应写“ON冷输入/ON热输入”或“命中诊断”，不能写“今日OFF/ON性能排名”。历史OFF仍然来自历史真实运行，不能把cold ON改名补出一个OFF。

## 可直接展示的代码线索

以下是安装文本或已批准源码包的定位点，供报告展示调用关系；**不是新增补丁，也不是逐指令硬件证明**：

- 已批准Python源码包`output/ks_ttft_prepare_m8_m137_20261020/approved-diag-python-sources.tar.gz`：`vllm_ascend/attention/sfa_v1.py:618、716–717`的`local_num_heads`；1733–1736的非CP hidden-state gather与1782之后的CP packed-KV gather，体现两条分工不同。
- 同包`vllm_ascend/device/device_op.py:720–754`：实际SFA调用，TND/PA_BSND、INT8 packed KV、key/value共享KV、tile128。不要套最新版示例替换当前API。
- [安装tiling文本](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/sfa/installed_native/csrc/attention/kv_quant_sparse_flash_attention/op_host/kv_quant_sparse_flash_attention_tiling.cpp:434)：`mBaseSize=gSize`，heads进入query内M维。
- [安装kernel文本](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/sfa/installed_native/csrc/attention/kv_quant_sparse_flash_attention/op_kernel/kv_quant_sparse_flash_attention_kernel_mla.h:571)：按query数量分给24个AIC组，不是按实际选中KV数量均衡。
- [KV聚合与反量化](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/sfa/installed_native/csrc/attention/kv_quant_sparse_flash_attention/op_kernel/kv_quant_sparse_flash_attention_service_vector_mla.h:749)：每query的离散KV聚合、类型转换与scale处理；准备结果再供该query多个heads使用。
- [小矩阵同步分支](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/sfa/installed_native/csrc/attention/kv_quant_sparse_flash_attention/op_kernel/kv_quant_sparse_flash_attention_service_cube_mla.h:689)：MM1的`(m/16)*(n/16)<10`，MM2对应891行之后。

运行DB与安装catalog已经确认：已核对的PP1/PP2冷8K均记录同一完整kernel名、key1158、MIX_AIC、AIC:AIV=1:2。动态tiling payload、有效长度/索引值以及源码到`.o`的构建对应仍未闭环，不能用名称相同宣称工作量、访问次数或指令完全相同。

## 事实分级与报告禁用表述

“已观察”用于正式结果、真实日志、profile和只读运行记录；“安装源码推断”用于调用/分支及工作模型；“理论解释”用于尚未拆份额的因果推测；`NOT_VERIFIED`用于尚未验收。历史配置仅适用于其记录日期，不能当当前状态。

应避免：cold ON叫OFF；heads更多就按比例多搬KV；逻辑选择数叫实际HBM流量；handler包络叫纯DMA；P窗口叫纯Prefill；客户端首token后耗时叫纯D执行；各rank设备时长相加叫请求时间；把TP/CP及环境同时改变说成纯PP效应。`KS-MEM-001`保持OPEN。
