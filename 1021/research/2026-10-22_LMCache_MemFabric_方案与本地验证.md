# LMCache 适配 MemFabric：方案与单机 A3 验证

更新：2026-10-22。状态：源码与公开资料研究完成；方案建议，尚未实施；A3 实机全部 NOT_VERIFIED。

## 1. 已确认的目标和边界

- 这是实际代码适配项目，不是仅通过已有配置启用后端。
- 不引入 MemCache。LMCache service 负责 KV 管理，MemFabric 负责池化内存与数据搬运。
- 现有一台用户所称“16 卡”的 A3，机内 HCCS 可用，缺跨服务器交换机/线缆。
- 实际物理卡、chip、逻辑设备、SIO/HCCS 分组，需要读机器拓扑后确认；不能按卡号推断。
- 用户希望独立内存池，并说明“就是 LMCache 的 service”。本方案将其落实为：LMCache 独立进程是缓存控制主体，BM 是其后端；第一阶段不要求多台 LMCache service 共同管理一个池。
- 本会话尚未连接 A3；没有安装、升级、改动服务或执行硬件实验。

## 2. 已有尝试：必须修正上一轮结论的范围

未发现公开的 LMCache 直接 MemFabric 后端，不等于没有类似适配。

完整查询范围与结果见 [已有尝试调查](2026-10-22_prior_art.md)。重要先例：

| 已有工作 | 查到的证据 | 对本项目的作用 | 边界 |
| --- | --- | --- | --- |
| MemFabric 官方 Mooncake UB 补丁 | `patches/mooncake/ub_transport.md`、`tag_v0.3.9.patch` | BM 内存怎样交给上层对象分配器、GVA/用户地址如何转换、池与传输分工 | 针对旧 Mooncake 版本，不可认定与当前 LMCache MP 直接兼容 |
| Mooncake TENT MemFabric Transport | PR #4682，2026-10-16 合入 | 当前 upstream 的 MemFabric TRANS 封装、协议和生命周期 | 作者报告的是 A5/SGLang 场景，不是 LMCache/A3 HCCS 验证 |
| Mooncake SDMA 修正 | PR #4834，查询时尚未合入 | SDMA 下的 Host 分配、GVA 和注册约束 | 属于待合入修复，不能当成已发布稳定能力 |
| vLLM-Ascend MemFabric 传输工具 | `tools/test_memfabric_pd_read.py`、`utils/memfabric_transfer_engine.py` | NPU/Host 缓冲区、读传输与双进程验证参考 | P/D 传输没有完整 KV 池对象语义 |
| SGLang MemFabric Host allocator | PR #38826 | Host 地址被设备访问时的映射/分配问题 | 稀疏 KV offload 不等于完整跨实例 KV 池 |

来源：

- [MemFabric 官方旧 UB 适配说明](https://github.com/Ascend/memfabric_hybrid/blob/master/patches/mooncake/ub_transport.md)
- [旧适配补丁](https://github.com/Ascend/memfabric_hybrid/blob/master/patches/mooncake/tag_v0.3.9.patch)
- [Mooncake #4682](https://github.com/kvcache-ai/Mooncake/pull/4682)
- [Mooncake #4834](https://github.com/kvcache-ai/Mooncake/pull/4834)
- [vLLM-Ascend 读传输工具](https://github.com/vllm-project/vllm-ascend/blob/main/tools/test_memfabric_pd_read.py)
- [SGLang #38826](https://github.com/sgl-project/sglang/pull/38826)

不要混配两代适配：旧 UB 补丁使用 `USE_UB` / `protocol=ub` / `MF_OP_TYPE`；新 TENT 使用 `USE_TENT`、`USE_MEMFABRIC`、`MC_USE_TENT`、TENT 配置和 `MF_PROTOCOL`。新 TENT 合入不能证明旧 UB ABI 仍成立。

## 3. 三种接入位置及建议

### A. LMCache service + MemFabric BM L2 后端（本项目首选）

```text
vLLM-Ascend 实例 A ─┐
                   ├─ LMCacheMPConnector → 同机 LMCache service
vLLM-Ascend 实例 B ─┘                          │
                                             ├─ 现有 L1 缓存与调度
                                             └─ 新增 MemFabricL2Adapter
                                                  │
                                                  └─ BM / SDMA → 各 BM rank 贡献的 DRAM
```

这是逻辑结构，`MemFabricL2Adapter` 是拟开发名称，不是已有官方插件。

选择理由：目标是可写可读的独立池，L2 store/lookup/load 正好覆盖该语义；保持推理引擎与现有 NPU KV gather/scatter 的改动较小。单机第一阶段，一个 service 统一负责池中对象，不需要先开发分布式目录服务。

注意，独立 service 进程不自动等于跨主机数据面。当前 MP 的本机设备 IPC 部分不能简单通过修改 server IP 变成跨机传输。将来多计算节点部署，需要明确每节点本地 service 与池的关系，再增加多 service 目录/租约协作；本轮不把这些当成已实现。

### B. LMCache P2P TransferChannel + MemFabric TRANS（备选，语义不同）

LMCache MP 已有 P2P 查询、锁定、读取与 peer 发现，可以新增 MemFabric 传输实现，复用对象管理。这适合各 service 持有自己的 L1，其他 service 从它读取。

但当前 P2P L2 adapter 明确不向远端写入、不做远端对象分配。不能把“读别人的已有 L1”当成“向独立空闲内存池存储 KV”。另外，现有 L1 Host 地址能否直接注册到目标 MemFabric SDMA 路径，可能要求同时适配 allocator。

### C. LMCache → Mooncake Store → MemFabric（现有代码参考/对照基线）

可复用 Mooncake 对象目录与分配器，减少自建存储语义。旧 BM 补丁和新 TENT 是实质先例。但这增加 Mooncake 依赖，且有版本、ABI、L1 注册、SDMA 分配未决项。用于研究参考或有明确复用收益时的验证分支，不将它当成本项目直接适配已完成。

来源：

- [LMCache-Ascend MP 结构](https://github.com/LMCache/LMCache-Ascend/blob/main/docs/mp_mode/README.md)
- [LMCache 外部 L2 插件](https://github.com/LMCache/LMCache/blob/dev/docs/design/v1/distributed/l2_adapters/plugin.md)
- [P2P L2：只读和无远端存储](https://github.com/LMCache/LMCache/blob/dev/docs/design/v1/distributed/l2_adapters/p2p_l2_adapter.md)
- [TransferChannel 扩展接口](https://github.com/LMCache/LMCache/blob/dev/docs/design/v1/distributed/transfer_channel/overall.md)

## 4. 首批代码改动边界

先锁定 A3 当前 LMCache、LMCache-Ascend、vLLM、vLLM-Ascend、MemFabric、CANN、驱动组合及实际源码提交。下述接口针对已查到的 MP 路线；若机器现用旧 in-process 版本，需要映射到该版本 RemoteConnector，而非直接升级整套环境。

| 模块 | 首版职责 | 尽量不修改的部分 |
| --- | --- | --- |
| 新增 MemFabric L2Adapter | 对接 store、lookup-and-lock、load、unlock、完成通知、close | vLLM 调度器、模型算子、LMCache 核心策略 |
| BM 会话封装 | initialize/create/join、地址查询、copy、退出；固定 device/rank/pool 配置 | MemFabric 内核与驱动 |
| 单 service 池内对象索引和槽位管理 | LMCache ObjectKey → rank/offset/length/layout/epoch/state；容量与引用保护 | 第一版不另建分布式 metadata 服务 |
| 加载与配置 | 使用官方 plugin 的 module_path/class_name，依赖延迟加载 | 第一版通常无需更改 LMCache factory |
| 目标验证工具 | 数据 round-trip、强制 L1 miss、对端 rank 读回和推理恢复 | 不新建通用测试框架 |

初版可将实现放在 LMCache-Ascend 的新后端模块或独立小包，通过现有 plugin 机制加载。目录位置在实际 checkout 和版本确认后决定，不凭空假设现有包布局。

关键契约：

1. store 的输入 MemoryObj 由 LMCache 持有；传输完成前不得释放其读锁。先分配槽位，完成写入，再发布 READY。
2. lookup 成功必须保护对象，直到 load/unlock；不能让地址在读期间被复用。
3. load 写入 LMCache 提供的目标 MemoryObj；完全完成后才能返回成功 bitmap。
4. 异步超时并不等于设备停止访问内存。必须等待/取消并确认 quiescence，再释放或复用。
5. `close()` 必须先排空传输再解除映射；不要让 BM 全局 init/uninit 破坏 LMCache 已有 NPU/IPC 上下文。
6. 单进程多线程/多 NPU 下，要明确 BM 执行线程的设备上下文。先验证和 LMCache NPU runtime 共存；确有冲突时再考虑隔离 I/O 进程。
7. 首版固定容量、固定成员、单 service authority；restart 视为易失缓存重建，不承诺持久化、热扩容或自动容灾。

官方 L2 示例：[external adapter](https://github.com/LMCache/LMCache/tree/dev/examples/lmc_external_l2_adapter)。
接口契约：[L2 overall](https://github.com/LMCache/LMCache/blob/dev/docs/design/v1/distributed/l2_adapters/overall.md)。

## 5. 16 卡 A3 的分阶段验证

不要第一轮占满 16 个可见设备。先找到一组 HCCS/HCCS_SW 相连、不是仅 SIO 相连的设备，分配实验进程；推理先用小模型 TP1。具体设备集合按库存和运行任务选取，不预设 0/1 就是 HCCS。

| 阶段 | 实验 | 成功证据 | 不代表什么 |
| --- | --- | --- | --- |
| T0 拓扑与版本 | 只读盘点芯片映射、HCCS/SIO、进程占用和软件版本 | 明确可用设备、物理映射、版本与真实加载路径 | 未运行任何传输 |
| T1 MemFabric 单 rank | 官方单卡 SDMA 生命周期与 DRAM 写入读回 | 分配/映射/读写/销毁正常，数据逐元素或逐字节一致 | 没有证明跨设备/跨机 HCCS |
| T2 同机跨 rank | 两个独立进程绑定不同设备，使用另一 rank 贡献的 DRAM | 两方向数据一致，实际选中 SDMA，无隐藏备用协议 | 跨 rank 仍然是同一物理主机 |
| T3 Adapter 对象级 | 无模型：store→清除源影响→lookup/lock→load→unlock | 真实 MemoryObj 数据正确；重复 key、容量耗尽、读锁期间回收规则正确 | 不等于 NPU KV 布局恢复已正确 |
| T4 LMCache service 推理 | 两个引擎共用一个 service，A 写池，B 强制 L1 miss 后恢复 | BM load 有真实字节；B 加载到 NPU；输出正确、Prefill 重算减少 | 不等于多 service 分布式目录已实现 |
| T5 有限并发 | 逐步增加服务请求/设备，观察拷贝与推理竞争 | TTFT、实际传输字节/时间、HBM/DRAM峰值、吞吐 | 不用单机数字推算跨机规模收益 |

### T0：只读盘点起点

在实际 A3 主机/目标容器中执行；当前会话尚未执行：

```bash
npu-smi info
npu-smi info -m
npu-smi info -t topo
python3 -m pip show lmcache lmcache-ascend vllm vllm-ascend memfabric-hybrid torch torch-npu
```

补充读取现有 CANN、驱动与固件信息、容器标签和源码提交，不以自动升级为前提。`npu-smi info -t topo` 是官方 A3 查询命令，输出区分 SIO、HCCS、HCCS_SW；映射物理/逻辑 ID 后再选实验卡。

来源：[A3 npu-smi 拓扑命令](https://www.hiascend.com/doc_center/source/zh/HDK/2610/A3/A3npu/npusmia3_0072.html)、[A3 HiAM/SIO/HCCS 层级](https://github.com/Ascend/mind-cluster/blob/master/docs/zh/scheduling/04_usage/03_basic_scheduling/01_affinity_scheduling/03_ascend_ai_processor_based_affinity.md)。

### T1/T2：官方样例怎样用

- [单卡 SDMA](https://github.com/Ascend/memfabric_hybrid/tree/master/examples/memory_pool/03_optimization/03_device_sdma)：当前样例 world_size=1，4GB DRAM；可作为初始化/销毁与拷贝参考。不能从单 rank 测试宣称已走过跨设备 HCCS。
- [单机多卡 DRAM](https://github.com/Ascend/memfabric_hybrid/tree/master/examples/memory_pool/02_scale_out/01_single_node_multi_device_dram)：当前代码 world_size=2，两个进程；默认使用 DEVICE_RDMA，包含 H2G→对端 G2G→G2H 验证。
- 因此应在保留原样例作对照的基础上，为本实验将协议明确配置为 SDMA、绑定实际 HCCS 设备对，核查当前版本成员容量与地址约束。不能因为目录叫“单机多卡”就声称默认使用 HCCS。
- 强制单一目标协议用于链路验收。MemFabric compose data op 支持按可用协议依次尝试；如果允许多个协议，最终成功不能证明首选协议成功。
- payload 各块使用不同模式，读回使用独立目标范围，验证全部块。官方性能样例复用 src/dst buffer，其吞吐演示不能替代本适配的完整对象正确性验证。
- 补一条本地 NPU HBM↔对端 rank DRAM 的验证，以覆盖最终 KV 数据的真实内存类型。不要只测 host↔host。

来源：[协议选择和 fallback 代码](https://github.com/Ascend/memfabric_hybrid/blob/master/src/hybm/csrc/data_operation/host/hybm_compose_data_op.cpp)。

### T4：如何排除“其实只命中了 L1”

两引擎接同一个 LMCache service 时，B 第一次请求并不保证 service L1 未命中。

实验顺序：

1. 使用固定模型、tokenizer、dtype、KV layout、TP 配置和前缀，先产生无缓存基线。
2. A 请求完成，等待插件 store 完成且索引为 READY，记录 key、owner rank、offset 和真实写入字节。
3. 使用该版本支持的定向 L1 驱逐/删除方式移除目标对象，仅保留 BM 副本。若没有合适接口，新增仅限实验的定向入口，不清空或重启整个 service。
4. B 发相同前缀；确认 vLLM 本地 prefix cache 不能掩盖外部加载，记录 service 的 L1 miss、MemFabric L2 hit 与真实读入字节。
5. 检查目标 NPU KV 恢复与确定性推理结果，并核对实际被复用的 token/chunk 数量。TTFT 仅作辅助证据。
6. 做一个不存在 key 的负对照，确认走 miss/recompute，避免伪命中。

## 6. 路径和性能边界

从 L2 接入，逻辑数据流是：

```text
推理 NPU KV → LMCache L1 → MemFabric BM 池
MemFabric BM 池 → LMCache L1 → 推理 NPU KV
```

当前 MemFabric SDMA 实现中的 `CopyLH2GH`/`CopyGH2LH` 还会分配临时 HBM：

```text
普通 Host buffer → 临时本地 HBM → BM GVA
BM GVA → 临时本地 HBM → 普通 Host buffer
```

这是当前源码中的具体路径，不是所有协议/配置都会走相同路径。需要分别测正确性和资源占用；不能把“使用 SDMA”直接说成零拷贝或一次拷贝。

来源：[hybm_data_op_sdma.cpp](https://github.com/Ascend/memfabric_hybrid/blob/master/src/hybm/csrc/data_operation/host/hybm_data_op_sdma.cpp)。本轮读取 MemFabric master 树提交为 `004d9317289fe99bd6bf13def0500b3fa3795ccc`，后续实施应固定实际版本。

如果性能目标明确要求远端 DRAM 直接进 NPU、绕开 L1，就需要第二阶段设计 BM-backed allocator / NPU transfer context / gather-scatter 与 GVA 关系。它不是首版普通 L2Adapter 自动获得的能力。

## 7. 单机通过后可报告的结论

- 可以报告：LMCache 与 MemFabric API、对象生命周期、跨 rank 数据存取及 NPU KV 恢复在指定 A3 单机配置下通过。
- 有协议日志、拓扑和执行证据时，可以报告：指定机内设备对上的 HCCS/SDMA 路径通过。
- 不可报告：跨服务器 HCCS-L2、多个主机的 DRAM 容量扩展、跨机延迟/带宽、交换网络故障恢复已通过。
- 同机两个容器/进程是两个软件参与者，不是两个物理服务器。

## 8. 建议的首轮交付

1. 固定版本和源码提交的环境记录。
2. 两进程 BM SDMA 最小验证工具及原始结果。
3. 可加载的 MemFabric L2Adapter，最小单 service 池内索引/分配与生命周期处理。
4. 明确强制 L1 miss 的 service 推理验证记录。
5. 实测复制路径和待补跨机验证项。

这五项是本适配的具体工作产物，不包含重做推理框架、添加 MemCache、建立通用 CI 或预先设计复杂容灾系统。
