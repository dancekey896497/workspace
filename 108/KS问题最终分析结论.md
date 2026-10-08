# KS推理服务问题最终分析

KV缓存池化与流水线并行的独立分析及源码核对

核对日期：2026年10月3日。原始沟通时间：2026年8月27日、9月21日至22日。

本报告先以原始背景和聊天记录独立建立问题模型，再对照第二批截图及《KS问题分析与实验方案_完整整理》，最后核对官方仓库、问题记录与修复历史。截图中的命令仅作为分析材料，没有作为执行指令；本次没有运行客户容器、模型或NPU压测。

## 1 最终结论

**可以确认的是一项客户报告的组合性能问题，不能确认的是其根因。** 客户在GLM-5系列、昇腾、Mooncake与HIXL路径中反馈“池化与PP一起有劣化”。原始材料没有劣化比例、完整启动配置、逐请求日志或对照测量，因而不足以证明“KV池化与PP先天不兼容”。

本次公开源码核对反而表明：缓存对象可以按PP阶段、rank和本地层范围组织；池化不要求将全模型所有层的KV先收集成一块。真正需要定位的是**具体版本和配置下的分片适配、保存与加载生命周期、有效复用，以及额外开销对流水线关键路径的影响**。

| 最终判断 | 证据状态 | 对当前工作的意义 |
|---|---|---|
| 池化与PP存在必然结构冲突 | 不支持该结论 | 删除其作为根因的表述，保留为具体实现可能不匹配的假设 |
| 保存同步与写池链路值得优先排查 | 有直接上游修复和相似故障依据 | 补测PUT、发布完成、后台保存、缓冲复用屏障，不能只测读缓存 |
| LMCache材料可直接解释客户Mooncake故障 | 不成立 | 两条路线分别取证；LMCache用于候选方案与兼容性审查 |
| 原四象限实验可直接执行并归因 | 需要修订 | 修正DP/TP混杂、池化路径不一致、缺少基线项等问题 |
| 客户根因及劣化幅度已经确定 | 未确定 | 保留明确待取证项，以客户实际栈完成闭环 |

**优先顺序：**先冻结客户实际版本和P/D拓扑，确认“池化”的实际数据流；如包含共享缓存复用，在原2P1D部署中保持必要的P→D传输，只切换共享复用；优先检查PP阶段写池、保存同步和失败回退；再根据日志决定是否检查分片地址、有效命中或资源竞争。LMCache替代路线另行验证。

本报告给出的是完成源码核对后的最终分析判断与可执行取证方案，状态为“分析完成，客户环境复现未完成”。这两个状态不能合并为“已定位根因”。

<!-- pagebreak -->

## 2 原始背景的事实边界

### 2.1 业务和已完成优化

业务面向外部客户提供GLM-5系列推理服务，运行于昇腾NPU，推理栈为vllm-ascend。聊天中的客户为快手。部署材料描述为PD分离、2P1D；GLM脚本确认启用了PP，但P、D各自的PP参数仍需查看实际启动文件。

D侧已经按负载调整并行策略，反馈效果明显且优于推荐基线。P侧做了DP实例间KVC亲和，但实现较粗糙、收益有限。两者说明优化进展主要在decode侧，不能据此断言prefill的具体瓶颈，更不能直接归因到缓存传输。

整体比值由1/3.5变为1/3.2。仅在同一指标、同一基准下，算术上的相对改善为约9.4%，从约28.6%到31.25%。原始记录未定义该“整体效率”的分母和采样口径；它不是已经核实的吞吐提升或时延下降幅度。

### 2.2 客户反馈与团队验证缺口

聊天明确说客户上层是Mooncake，依赖HIXL的fabric mem模式，并反馈与PP一起使用有劣化。另有参与者表示“确实没和pp一起测过”。准确含义是客户反馈与团队缺少联合验证同时存在，不能改写为“客户没有测试”，也不能把客户反馈当成已完成的因果实验。

第一批截图既含聊天原文，也含此前撰写的背景解释；“全层整块与PP结构冲突”等属于整理者分析，证据等级低于聊天中直接可见的部署和现象陈述。

### 2.3 已有复现入口

| 项目 | 原始记录 | 当前可用性 |
|---|---|---|
| 容器与节点 | test-dpv4；12(p1)、137(p2)、25(d) | 仅有文字记录；数字是标记，不能解释为节点数量 |
| 脚本目录 | /mnt/sdb/ks98/dsv4；GLM为/mnt/sdb/ks98/glm5 | 当前工作区没有这些脚本正文 |
| 服务启动 | start_prefill_node1.sh、start_prefill_node2.sh、start_decode.sh | 需保留三侧完整参数和环境变量 |
| 路由与压测 | p1上运行start_proxy.sh、bench.sh | 数据集定义在bench.sh中，尚未取得 |
| 负载与参考 | ks测试负载.zip；glm5.1+vllm参考.md | 只有截图中的文件名，未取得原附件 |

当前不能确认的关键项包括：具体模型与量化版本、实际connector组合、P/D的TP/DP/PP布局、PP分层、是否启用MTP/EP、缓存命中口径、失败策略、HIXL/CANN版本以及劣化发生在TTFT、TPOT还是吞吐。

<!-- pagebreak -->

## 3 独立分析的问题模型

### 3.1 三类缓存路径必须区分

本地前缀缓存是在当前实例内复用已有KV；外部共享缓存是在实例之间通过存储与传输复用KV；PD传输则是将本次请求在P侧生成的KV交给D侧继续解码。这三类能力可能同时存在，但收益来源、数据流和开关不同。

2P1D表示服务角色和副本数量，PP表示一个模型实例内部按层划分阶段。“两个P组”不能等同于“PP2”；一个跨两个物理节点的PP实例也不等同于两个独立P副本。

在当前上游，外部缓存可由AscendStoreConnector加Mooncake后端实现；P→D交接可由MooncakeConnectorV1承担，二者可经MultiConnector组合。客户实际是否使用这组实现，仍要以完整kv_transfer_config为准。[依据：官方KV Pool职责说明](https://github.com/vllm-project/vllm-ascend/blob/4ebb3090fc3c13c26557dcaae353882e70246649/docs/source/user_guide/feature_guide/kv_pool.md#L5-L9)

### 3.2 池化收益取决于有效复用和关键路径成本

一次查找显示命中，不代表所有PP阶段需要的KV均可用；传输成功也不代表已写入正确的NPU缓存，更不代表调度器实际跳过了对应prefill计算。需要连通“查找命中→分片齐全→加载完成→实际少算”的证据链。

池化能够带来的收益，是实际省下的计算及可能获得的容量收益；代价包括索引查找、GET和PUT、缓存发布、注册与拷贝、同步等待及失败重算。判断应看关键路径上未被计算重叠隐藏的净成本，不能把并行执行的所有操作耗时简单相加。

对PP而言，某个阶段的额外等待、负载不均或保存屏障就可能拖慢整条流水线；无需先假设存在“全模型KV收集”。对PD而言，P侧节省计算也未必变成端到端收益，D侧服务能力、路由排队和P→D交接都可能限制吞吐。

### 3.3 路由亲和与共享缓存可能相互影响

P侧KVC亲和可提高本地或近端命中，也可能把请求集中到少数副本，引入排队与负载不均。共享池能减少重算，但可能增加远端读写和共享链路竞争。因此需要同时观察每个P副本的有效复用率、排队时间、负载和远端流量，不能只追求总命中率。

独立分析阶段的判断是：现场现象可以由实现缺口、额外开销或实验混杂解释；应先确定实际链路与故障签名，再选择验证假设。该判断随后得到公开源码的支持。

<!-- pagebreak -->

## 4 客户路线的源码结论

### 4.1 池化没有全模型整块这一强制前提

Mooncake Store接受字符串key与多个buffer及其长度；Transfer Engine处理源地址、目标区域、偏移和长度，底层不要求一个对象对应完整模型的所有层。由此可按PP阶段组织对象，但底层通用接口本身不保证上层适配正确。[Store多buffer接口](https://github.com/kvcache-ai/Mooncake/blob/0d1a8040faebb7c127c8901840a38c2ff57e80c5/mooncake-store/include/pyclient.h#L382-L410)；[传输请求结构](https://github.com/kvcache-ai/Mooncake/blob/0d1a8040faebb7c127c8901840a38c2ff57e80c5/mooncake-transfer-engine/include/transport/transport.h#L60-L80)

vllm-ascend的AscendStore进一步提供直接证据：PoolKey包含pp_rank等分片维度，worker按本阶段实际KV张量建立地址、stride和每块字节数；D回写P侧布局时存在按PP partition拆分key、地址与长度的处理。因此“命中后必须先gather全模型缓存”不是普遍实现事实。[PoolKey定义](https://github.com/vllm-project/vllm-ascend/blob/4ebb3090fc3c13c26557dcaae353882e70246649/vllm_ascend/distributed/kv_transfer/kv_pool/ascend_store/metadata.py#L120-L163)；[本地KV几何](https://github.com/vllm-project/vllm-ascend/blob/4ebb3090fc3c13c26557dcaae353882e70246649/vllm_ascend/distributed/kv_transfer/kv_pool/ascend_store/pool_worker.py#L963-L987)

### 4.2 PP支持是版本和路径相关的

当前MooncakeConnectorV1读取P侧并行拓扑，但明确断言decode侧PP为1。这意味着原整理稿“P/D组内都开PP”的表述需要降为待核实项；若客户D侧确实PP大于1，要确认其connector、runner、自研改动或分支，不能直接套用当前默认实现。[D侧约束](https://github.com/vllm-project/vllm-ascend/blob/4ebb3090fc3c13c26557dcaae353882e70246649/vllm_ascend/distributed/kv_transfer/kv_p2p/mooncake_connector.py#L2222-L2242)

当前Mooncake Store的layerwise后端也有PP支持及拓扑校验，但相关支持于9月22日合入，晚于9月21日的客户反馈。当前代码能反驳“原理上永不支持”，不能证明客户当时所用版本已经支持。[当前layerwise校验](https://github.com/vllm-project/vllm-ascend/blob/4ebb3090fc3c13c26557dcaae353882e70246649/vllm_ascend/distributed/kv_transfer/kv_pool/ascend_store/backend/mooncake_layerwise.py#L177-L198)

### 4.3 HIXL fabric mem需按角色和版本核对

当前Mooncake代码只允许Store初始化路径启用相应fabric mem选项，普通P2P传输引擎不会直接继承这个开关；Store和PD传输还可按角色选择不同链路。这说明“底层都是HIXL”不能替代实际传输路径的确认。[Store专用开关](https://github.com/kvcache-ai/Mooncake/blob/0d1a8040faebb7c127c8901840a38c2ff57e80c5/mooncake-transfer-engine/src/transport/ascend_transport/ascend_direct_transport/ascend_direct_transport.cpp#L187-L201)

需核对实际内存分配和注册方式、D2H/H2D/D2D路径、buffer pool及异步配置。这里的约束属于具体版本的工程实现，不能上升为PP与池化的数学或架构必然性。

<!-- pagebreak -->

## 5 公开故障和修复提供了哪些线索

下列日期取官方GitHub记录，合并日期按UTC。它们用于检查客户提交是否包含相关变更；“公开问题相似”不等于“客户触发同一问题”，“PR已合并”也不等于“客户性能已经验证”。

### 5.1 最直接的写池故障和保存开销

[Issue 11478](https://github.com/vllm-project/vllm-ascend/issues/11478)报告GLM-5.1、PP2与Mooncake KV Pool组合在P侧producer-put阶段发生TRANSFER_FAIL，涉及pp_rank:1。它与客户现象高度相关，但报告环境为A2、64卡、4P4D和0.23测试镜像，不能与客户2P1D直接等同。该issue于7月6日创建、8月2日因缺少反馈自动关闭，未找到明确关联的修复；关闭状态不能解释为已解决。

[PR 15636](https://github.com/vllm-project/vllm-ascend/pull/15636)于9月20日合并，直接减少PP加AscendStore的保存同步和深拷贝开销：避免同一步立即等待保存队列，改在下一步重用缓冲前同步。这是“写池、发布和保存等待值得优先计时”的具体依据。其收益仍需在客户负载上测量，不能用PR标题代替客户根因。

[PR 12763](https://github.com/vllm-project/vllm-ascend/pull/12763)于7月27日合并，处理PP加MTP下非最后阶段的connector finalize及AscendStore握手相关行为。若客户启用MTP，应将阶段完成通知与保存生命周期列入版本核对；不能声称它就是11478的修复。

### 5.2 聊天前后的支持变化

| 变更 | 合并时间 | 与本问题的关联及边界 |
|---|---|---|
| vllm-ascend 17183 | 9月22日 | Mooncake layerwise KV Pool增加PP支持；晚于9月21日反馈 |
| vllm-ascend 17246 | 9月24日 | 修正PP下layerwise cache group索引；主要示例为DSv4与MemCache，不能移植为GLM根因 |
| Mooncake 3955 | 9月9日 | fabric mem回到Store专用路径；说明非Store D2D受HIXL版本能力影响 |
| Mooncake 4286 | 9月23日 | 修正direct ACL VMM fabric内存注册路径；晚于聊天，且作者说明未做真实NPU端到端验证 |

对应来源：[17183](https://github.com/vllm-project/vllm-ascend/pull/17183)、[17246](https://github.com/vllm-project/vllm-ascend/pull/17246)、[3955](https://github.com/kvcache-ai/Mooncake/pull/3955)、[4286](https://github.com/kvcache-ai/Mooncake/pull/4286)。

以上记录提高了“连接器适配、保存同步、内存注册与失败回退”的排查优先级，并未建立客户故障与任一PR的一一对应关系。应检查提交包含关系与实际生效代码，再评估小范围补丁；本报告不建议为尝试修复而直接更换整个软件栈。

<!-- pagebreak -->

## 6 候选原因和可证伪条件

优先级按客户路线相关性、公开证据直接性和取证成本排序，不表示已经估计出发生概率。先检查启动和输出正确性，再讨论性能。

| 优先项 | 候选原因 | 支持线索 | 最小验证与排除条件 |
|---|---|---|---|
| 1 | PP阶段写池或保存同步开销 | 11478、15636、12763 | 逐阶段PUT、队列、发布完成和缓冲复用等待；若无失败且关键路径等待很小，应降级 |
| 2 | 层或rank分片元数据不匹配 | PoolKey及stage本地地址的适配要求 | 核对层范围、cache group、shape、stride、块字节、注册长度、键维度；用输出一致性检查区分正确性故障 |
| 3 | 命中承诺未变成有效复用 | 完整分片与调度跳过计算需联动 | 同请求比较逻辑命中、成功加载、实际少算token；若三者一致，转向成本分析 |
| 4 | Store与PD传输竞争或配置不匹配 | 角色链路分离及fabric历史变更 | 分开记录GET、PUT、P→D流量和失败；核对注册模式、通道、HIXL版本及初始化日志 |
| 5 | 流水线等待与路由负载不均 | PP关键阶段及P侧粗糙亲和策略 | 每stage利用率、每P队列、D等待、批量大小与路由分布；固定路由做对照 |
| 6 | 工作负载和实验口径混杂 | 尚无一致对照，原方案改变并行布局 | 固定长度、到达率、缓存状态及其他开关；看劣化是否仍存在 |

### 6.1 需要补齐的请求时间线

从请求到达开始，记录路由与P排队、外部lookup、GET与写回、prefill各PP阶段、PUT及缓存发布、P→D交接、D等待、首token和完成。部分动作会重叠，应按依赖关系找到关键路径，而非把各段平均时长直接求和。

PUT可能影响当前请求，也可能通过保存队列、下一步缓冲重用或缓存发布时间影响后续请求。只比较“命中请求的pull耗时”，会漏掉这一类已被上游修复记录指出的开销。

### 6.2 根因成立需要形成闭环

较强的根因证据应同时满足：同一部署下现象可重复；日志或时间线显示候选环节异常；一个针对该环节的受控变更改善异常及业务指标；恢复原条件能再次出现问题，或有等价的反事实对照。仅有静态代码分支、相关PR或一次跑分差值都不足以结案。

<!-- pagebreak -->

## 7 LMCache代码证据的复核

这一节审查的是第二批材料提出的替代路线。它可以发现LMCache自身的兼容性风险，但不作为客户Mooncake根因证据。已同时核对LMCache与LMCache-Ascend的v0.4.4，以及本次检出的公开主线快照。

### 7.1 E1至E8的最终判定

| 项目 | 已核实的代码事实 | 对原解释的修正 |
|---|---|---|
| E1 后端互斥 | P2P或PD与use_layerwise同时启用会被拒绝 | 校验没有读取PP参数，不能推出PP本身不支持；截图配置为layerwise false且未开P2P/PD |
| E2 首rank保存 | MLA路径可仅首rank维护缓存，其他rank被标为passive | 它原本用于重复KV去重；须检查PP下作用域，不能直接宣布非首rank全部丢层 |
| E3 异步保存限制 | layerwise保存分支断言不能store_async | 适用于该分支执行时，并非所有PP服务启动必报错 |
| E4 失败重算 | P2P pull失败可将块标为invalid，交给vLLM重算 | 日志出现才证明请求触发；源码有回退不代表现场已回退 |
| E5 传输通道 | 支持hccl和hixl，示例有不同推荐等级 | 属于LMCache支持矩阵，不等于客户Mooncake fabric路径 |
| E6 搬运组装 | P2P后端有暂存、读写和接收逻辑 | 页式KV写入还涉及NPU connector，不能只测单文件 |
| E7 延迟拉取 | proxy对象支持延迟获取 | 仅相应路径生效，不是所有命中统一行为 |
| E8 观测标记 | 有NVTX装饰器，未装nvtx时可退化为空操作 | 不保证msprof自动显示完整lookup、pull、scatter分段 |

主要源码：[后端配置校验](https://github.com/LMCache/LMCache-Ascend/blob/19c13d849d6bcdea6316cdd8674fae210b3326f4/lmcache_ascend/v1/storage_backend/__init__.py#L69-L112)、[适配层失败回退](https://github.com/LMCache/LMCache-Ascend/blob/19c13d849d6bcdea6316cdd8674fae210b3326f4/lmcache_ascend/integration/vllm/vllm_v1_adapter.py#L68-L121)、[NVTX与空操作](https://github.com/LMCache/LMCache/blob/6fbec463e3c047fffb4e22c97508f03b057de3bc/lmcache/utils.py#L15-L27)。完整逐项链接保存在独立代码审查报告中。

### 7.2 PP不等于按层传输模式

PP阶段可以把自己负责的多层KV按token chunk统一保存，不需要强制use_layerwise为true。上游保存路径明确考虑每个PP阶段保存自己的KV，同步lookup会汇总TP和PP各rank的命中token并取最小值。这些代码说明两者并非原理互斥，也不代表任意模型、后端和版本都已经通过验收。[逐PP阶段保存](https://github.com/LMCache/LMCache/blob/6fbec463e3c047fffb4e22c97508f03b057de3bc/lmcache/integration/vllm/vllm_v1_adapter.py#L1174-L1193)；[命中完整性聚合](https://github.com/LMCache/LMCache/blob/6fbec463e3c047fffb4e22c97508f03b057de3bc/lmcache/v1/lookup_client/lmcache_lookup_client.py#L131-L150)

<!-- pagebreak -->

## 8 LMCache替代路线的具体风险

### 8.1 首rank判定和TP广播作用域不一致

在截图对应的classic connector、MLA及save_only_first_rank启用路径中，上游将metadata.worker_id设为parallel_config.rank，first_rank判定固定为全局worker 0；广播函数却来自get_tp_group。对DP1、TP8、PP2的常规布局，第二个阶段是全局rank 8至15，其TP组内源rank 0实际是全局rank 8。

vLLM v0.18.0源码已经交叉确认这些rank语义。因此，“只有全局0为active”与“每个TP组需要自己的发送者”存在明确的静态作用域不一致风险，值得在LMCache验证中优先检查。仍不能据此指定运行结果一定是死锁、丢层或退化，更不能把它迁移为客户Mooncake根因。

验证时逐worker记录global rank、TP rank、PP rank、metadata.worker_id、first_rank、use_mla、passive、层范围及storage_manager，再用同一前缀的连续请求检查各stage的保存、广播与实际命中。不要把关闭first-rank或打开layerwise直接当成修复；它们还会改变lookup worker、缓存容量和传输行为。

依据：[LMCache元数据和广播绑定](https://github.com/LMCache/LMCache/blob/6fbec463e3c047fffb4e22c97508f03b057de3bc/lmcache/integration/vllm/vllm_service_factory.py#L145-L215)、[first_rank定义](https://github.com/LMCache/LMCache/blob/6fbec463e3c047fffb4e22c97508f03b057de3bc/lmcache/v1/metadata.py#L53-L70)、[vLLM组内广播源语义](https://github.com/vllm-project/vllm/blob/bcf2be96120005e9aea171927f85055a6a5c0cf6/vllm/distributed/parallel_state.py#L593-L605)、[TP和PP分组](https://github.com/vllm-project/vllm/blob/bcf2be96120005e9aea171927f85055a6a5c0cf6/vllm/distributed/parallel_state.py#L1538-L1569)。

### 8.2 原实验没有配置跨实例池化

截图YAML配置local_cpu、use_layerwise false、store_async true，kv_role为kv_both。它表明本地缓存可保存和加载；在没有额外环境覆写时，enable_p2p和enable_pd默认为false，没有controller与peer配置，不能声称已覆盖跨实例共享或客户Mooncake路径。[P2P默认值](https://github.com/LMCache/LMCache/blob/6fbec463e3c047fffb4e22c97508f03b057de3bc/lmcache/v1/config.py#L131-L135)；[真正的P2P实例配置](https://github.com/LMCache/LMCache-Ascend/blob/19c13d849d6bcdea6316cdd8674fae210b3326f4/examples/kv_cache_reuse/share_across_instances/p2p_sharing/instance1.yaml#L1-L25)

### 8.3 硬件和版本表述需收紧

当前GLM-5.1指南写的是8张卡、每卡2芯片，共16个芯片，推荐布局为DP2乘TP8。改成PP2乘TP8不属于同一已验证配置；“16芯片”也不能写成“16张物理卡”。指南在本次主线存在，v0.4.4 tag中没有；指南建议安装v0.4.4，并不表示该tag内已经包含指南。

截图中的A2八设备一定能启动、权重约280GB，以及所有docker补丁都必须应用，均缺乏对应范围的验证。应按实际checkpoint、HBM、目标路径和版本判断；单机能启动不代表2P1D跨实例问题已经复现。[GLM指南的硬件与软件条件](https://github.com/LMCache/LMCache-Ascend/blob/19c13d849d6bcdea6316cdd8674fae210b3326f4/docs/recommended_deployment_guide/glm5.1/glm5.1-single-node-lmcache-deployment-guide-ddr-vs-hbm.md#L7-L63)

<!-- pagebreak -->

## 9 与此前问题分析文档的对比

此前DOCX是对截图内容的完整整理，并已将部分结论改为假设，补充了公式、变量和技术路线的说明。本次增加了独立分析及版本化源码核对，最终判断以本报告为准；原整理稿仍可作为原材料索引。

| 对比项 | 第二批材料的主张或方案 | 最终处理 |
|---|---|---|
| 问题本质 | 整块池化与PP分层构成结构冲突 | 撤回必然性结论；改为具体版本的分片、生命周期与成本问题 |
| 关键缺口 | 缺少池化与PP联合测量 | 保留；补充客户配置、真实负载、输出正确性与有效命中 |
| E1解释 | layerwise互斥证明PP兼容性问题 | 限缩为特定后端配置互斥，不等同PP |
| E2解释 | 首rank集中管理与PP天然冲突 | 改为MLA classic路径的具体rank作用域风险 |
| 观测链路 | lookup、pull、scatter | 扩展PUT、发布、保存屏障、P→D交接及流水线等待 |
| 路线选择 | 用LMCache实验定位客户池化问题 | 客户Mooncake复现优先；LMCache另做替代路线验证 |
| D组启动失败 | 可作为池化加PP复现结论 | 只能确认该配置不可用，不能复现“运行时性能下降” |
| 四象限 | 直接计算D减去B与C之和 | 原始成本需补A基线；并检查DP/TP等混杂 |
| 实验池化开关 | local_cpu加kv_both视为池化开启 | 只覆盖相应本地缓存；共享池与PD需单独配置 |
| A3配置 | DP2TP8切到DP1TP8PP2，称只改PP | 同时改变副本数和调度，属于部署方案比较 |
| A2配置 | TP8切到TP4PP2 | 同时改变TP，不能归因纯PP |
| 单机价值 | 可复现冲突，劣化是跨机下界 | 只保证可做部分路径验证，结果不具一般下界关系 |
| 命中率扫描 | 改prompt长度或轮数即视为目标命中率 | 固定总长度分布，构造复用比例，记录实际有效命中 |
| 复现标准 | 大于10%、三次方向一致 | 可作事先约定的工程阈值，不能替代方差、区间与SLO |
| 观测工具 | 有NVTX标记即能用msprof按阶段读取 | 需验证目标环境是否采集到事件，必要时补请求级计时 |

原方案中四象限、重复运行、分阶段计时、命中率与并发扫描仍值得保留。关键调整是先对准客户实际栈，再确保实验回答的是同一个问题，而非把替代环境的限制解释为原客户根因。

<!-- pagebreak -->

## 10 修订后的实验设计

### 10.1 第一轮直接回答客户问题

先确认客户所称“池化”是否包含跨请求共享复用或其他卸载路径。如包含共享复用，保留客户模型、量化、2P1D、P/D并行拓扑、MTP、EP、图模式、批量上限和路由策略，比较共享复用关闭与开启。必要的P→D KV传输必须保留；若一个开关同时关闭PD传输与共享复用，应先拆清配置，不能当成有效对照。

在相同负载下分别跑冷缓存、预热后稳定复用和低复用场景，记录业务指标及关键路径。先确认组合确实变差及主要发生环节，再决定是否需要扩展到无PP环境。这样可避免一开始换引擎或改并行布局而丢失原问题。

### 10.2 四象限只在可比条件下使用

| 组别 | PP | 共享缓存复用 | 作用 |
|---|---|---|---|
| A | 关 | 关 | 对照基线 |
| B | 关 | 开 | 无PP布局内的复用效果 |
| C | 开 | 关 | PP布局的无复用基线 |
| D | 开 | 开 | 客户关注的组合效果 |

对同口径原始成本Y，交互项为：**I＝YD－YC－YB＋YA**，等价于比较“PP下开复用的成本变化”和“无PP下开复用的成本变化”。若Y为耗时，正值表示在该加性尺度上出现额外成本；它不是未加限定的普遍“冲突损失”。

对吞吐，更直接的结果是分别报告B/A与D/C的增益比例，并结合相同SLO下的有效服务能力。尾分位数、比值与平均阶段时间不能混加。重复实验需报告波动或区间，不把三次同向当成统计显著。[交互与混杂的方法依据](https://www.itl.nist.gov/div898/handbook/pri/section5/pri594.htm)

### 10.3 分开部署决策和机制定位

固定总资源时，从DP2TP8切换为DP1TP8PP2会改变副本数；从TP8切换为TP4PP2会改变张量并行。它们可以回答“哪个完整部署更适合负载”，不能回答“PP单独导致多少损失”。若资源不允许保持DP和TP，就如实报告比较边界，不强行称为单变量实验。

单机用于验证启动、缓存完整性、rank作用域和局部等待；跨节点用于验证实际拓扑下的传输、竞争和端到端业务表现。节点数、链路和缓存容量改变后，劣化可能增大也可能减小，不能将单机数值自动标为跨机下界。

<!-- pagebreak -->

## 11 负载和观测要求

### 11.1 控制请求与缓存状态

固定输入输出token长度分布、生成参数、负载强度、请求数及持续时间。调整前缀复用比例时尽量维持总输入长度分布，通过预热指定前缀控制复用机会；0%、30%、60%、90%只能是目标档位，最终以实际有效外部token复用率为准。

分别记录本地APC命中、外部逻辑命中、加载成功、完整阶段覆盖及实际少算token。跨rank完整命中不能用某一个rank的命中率代替；具体算法应与实际分层和cache group语义一致。0%复用档仍是识别固定开销与资源竞争的必要对照，不能简单排除。

开放式到达率与封闭式并发上限回答不同问题。固定qps为0.3的试验不能直接量出最大吞吐；用户数也不等于稳态并发。重复同一数据和随机种子可能留下缓存，应记录清理、预热及运行顺序。[vLLM压测指标与负载说明](https://docs.vllm.ai/en/latest/benchmarking/cli/)

### 11.2 每组最少保留的数据

| 层面 | 最少记录项 | 用于区分什么 |
|---|---|---|
| 业务 | 成功率、tokens/s、TTFT、TPOT、E2E的P50/P99、既定SLO | 业务劣化类型；满载与未满载的差别 |
| 请求 | request_id、长度、到达率或并发、路由、逻辑命中、实际少算token | 请求是否可比，复用是否真实 |
| PP阶段 | 层范围、TP/PP rank、group、shape/stride、每块字节、KV就绪时间 | 分片完整性和最慢阶段 |
| 读写 | lookup、GET、PUT、发布、写回、队列等待、保存屏障 | 读成本、写成本及未隐藏的等待 |
| 传输 | Store与PD分别统计字节、调用数、重试、失败及回退 | 数据量、碎片化、共享资源竞争 |
| 资源 | 各stage利用率、HBM、Host缓存、P队列、D等待、链路状态 | 容量压力、流水线气泡与调度不均 |

TPOT和ITL在投机解码下不应无条件互换；跨进程时钟需对齐，或使用单侧可比较时间线。先确认profile中确实能看到所需事件，再依赖其作分阶段结论。

### 11.3 结果分类

不能启动归为配置或接口兼容问题；输出不一致归为正确性问题；失败回退归为可靠性与重算问题；有效复用正常而指标变差归为性能问题。每类需要不同证据，不能用统一的“兼容性劣化”掩盖差别。尾延迟样本量不足时，应保留不确定性。

<!-- pagebreak -->

## 12 当前待办和结案条件

### 12.1 按顺序执行的最小工作包

1. 冻结现场。收集P1、P2、D和proxy启动脚本，完整kv_transfer_config、环境变量、实际加载配置与各组件提交。确认D侧是否PP、是否MultiConnector，以及“池化开关”究竟控制什么。
2. 固定负载。取得bench.sh、ks测试负载.zip和GLM参考文件，明确1/3.2指标定义、基准和客户关注的SLO，固定本地与外部缓存状态。
3. 找到首次异常。保留同一请求各stage首次PUT/GET失败、pp_rank、块字节、注册长度、后台异常、发布和缓冲复用等待。先确认真实走到哪条链路。
4. 做客户栈开关对照。保持PD传输和其余优化不变，测共享复用开关；按第6节决策路径缩小问题。若现场只出现保存等待，优先评估15636相关变更是否缺失或被回退。
5. 做一项有针对性的验证。依据日志选择生命周期、分片元数据、注册方式或同步点中的一项，比较变更前后异常和业务指标。避免同时换镜像、并行布局和缓存引擎。
6. 如需LMCache替代方案，另建验收。先证明所需本地或P2P路径确实开启，再验证MLA加PP的rank作用域、正确性、有效复用及SLO；其结果单独报告。

### 12.2 尚未关闭的关键问题

| 开放问题 | 为什么影响最终归因 |
|---|---|
| 客户实际模型、版本、补丁及connector是什么 | 决定公开代码和修复是否适用 |
| PP开在哪一侧，层如何分配 | D侧限制、非均匀分层和MTP附加层会改变适配路径 |
| 劣化是写池失败、命中减少还是等待增加 | 分别对应可靠性、有效复用或性能成本 |
| 命中率是本地、远端、请求还是token口径 | 决定所谓池化收益是否真实产生 |
| 同负载同拓扑下是否仍劣化 | 排除DP/TP、路由、缓存残留和压测口径混杂 |
| 单点修正是否恢复业务SLO且输出正确 | 决定能否从“候选原因”升级为“根因已确认” |

### 12.3 可用于后续汇报的结论

KS问题应定义为“GLM-5在客户Mooncake与HIXL部署中，启用客户所称的KV池化后，与PP组合出现性能退化；具体池化路径及根因待现场确认”。公开源码不支持池化与PP存在必然结构冲突；公开相似故障和修复将排查重点指向PP阶段写池及保存同步、分片与完成协议、有效复用和传输资源竞争。原LMCache实验方案需要修正技术路线和对照条件，不能直接用于客户结案。完成现场版本核对与受控对照后，才能确定具体补丁、配置改动及实际收益。

<!-- pagebreak -->

## 13 版本和证据索引

### 13.1 本次源码快照

仓库位于当前工作区的repos目录，仅浅克隆和读取，未改源码、安装依赖或启动服务。以下提交用于固定本报告的源码证据，不代表客户所用版本。

| 仓库 | 检出分支和提交 | 额外核对版本 |
|---|---|---|
| Mooncake | main [0d1a8040faeb](https://github.com/kvcache-ai/Mooncake/commit/0d1a8040faebb7c127c8901840a38c2ff57e80c5) | 相关PR及聊天前后变更 |
| vllm-ascend | main [4ebb3090fc3c](https://github.com/vllm-project/vllm-ascend/commit/4ebb3090fc3c13c26557dcaae353882e70246649) | 相关issue、PR和合并时间 |
| LMCache-Ascend | main [19c13d849d6b](https://github.com/LMCache/LMCache-Ascend/commit/19c13d849d6bcdea6316cdd8674fae210b3326f4) | v0.4.4 [d241090322ea](https://github.com/LMCache/LMCache-Ascend/commit/d241090322eadfc39cd6f0003e86295010506080) |
| LMCache | dev [4abc421a55cc](https://github.com/LMCache/LMCache/commit/4abc421a55ccd965f48c00592a2513cbc95c01fb) | v0.4.4 [6fbec463e3c0](https://github.com/LMCache/LMCache/commit/6fbec463e3c047fffb4e22c97508f03b057de3bc) |
| vLLM | 通过官方源码核对rank语义，未另行克隆 | v0.18.0 [bcf2be961200](https://github.com/vllm-project/vllm/commit/bcf2be96120005e9aea171927f85055a6a5c0cf6) |

### 13.2 工作区材料

- research/01_独立分析_原始背景.md：先于对比整理稿形成的独立判断。
- research/02_Mooncake代码核对.md：Store、Transfer Engine、HIXL及fabric相关证据。
- research/03_LMCache代码证据审查.md：E1至E8、first-rank风险、配置和版本核查。
- research/04_vllmAscend链路审查.md：客户候选connector、PP支持、公开故障与修复时间线。
- research/05_实验设计独立审查.md：变量控制、指标与交互效应方法。
- output/KS问题背景整理.docx：第一批4张截图的背景整理。
- output/KS问题分析与实验方案_完整整理.docx：第二批12张截图的完整整理。

正文中的蓝色来源链接指向官方固定提交、公开issue或PR。客户原始附件的事实以用户截图为来源；未从文件名推测文件正文，未把仓库自述的测试结果当作本次硬件验证。

### 13.3 证据分级

“聊天明确陈述”用于确认客户反馈与材料入口；“固定源码确认”用于确认特定版本的接口和分支；“上游问题或修复”用于提高候选路径优先级；“分析推断”用于提出可检验机制；“客户实测”目前缺失。最终报告按这一边界使用证据，没有以推断替代现场结论。
