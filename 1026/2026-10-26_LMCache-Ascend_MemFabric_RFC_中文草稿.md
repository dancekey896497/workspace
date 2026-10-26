# [RFC] 支持 MemFabric KV 池：兼容 L2 接入与 NPU worker 直传

## 问题描述

希望讨论在 LMCache-Ascend 的 MP 模式下接入 MemFabric KV 池的实现方向。

目标是保留 LMCache service 的缓存管理能力，由 MemFabric 提供池内存与数据搬运，不额外引入 MemCache 作为对象管理层。我们考虑分阶段提供两条可选路径：

1. **兼容 L2 路径**：通过 L2 adapter 接入 MemFabric，复用现有 CPU L1 和 NPU↔L1 搬运。
2. **worker 直传路径**：worker 在自己的 NPU 上直接读写池，service 负责对象和完成状态，减少独立 CPU L1 payload 中转。

当前兼容路径的数据流为：

```text
写入：NPU KV → LMCache CPU L1 → MemFabric 池 DRAM
恢复：MemFabric 池 DRAM → LMCache CPU L1 → NPU KV
```

这条路径适合先验证 KV 池存取与缓存语义，但仍需要独立 L1 缓冲和额外搬运。尤其当 L1 已经能够满足前缀复用或 P/D 交接时，增加池可能只增加写入和维护成本，未必产生读池收益。

因此，希望同时讨论兼容接入方式与后续直传扩展点，而不把“池可以存取 KV”视为已经实现了减少中转的性能路径。

## 建议方案

### 1. 两条路径的关系

建议先保留兼容路径作为可回归的功能基线，再提供显式选择的直传路径。直传初期不改变现有 NPU 默认的 MP transfer mode。

| 项目 | 方案一：兼容 L2 | 方案二：worker 直传 |
| --- | --- | --- |
| 缓存管理 | LMCache service | LMCache service |
| 池数据搬运 | service 的 L2 adapter，CPU L1↔池 | worker 的 NPU↔池 |
| 独立 CPU L1 payload | 保留 | 命中池的对象不经独立 CPU L1 中转 |
| NPU KV 整理 | 复用现有路径 | 允许必要的 NPU gather/scatter 缓冲 |
| 首要目标 | 功能兼容、对象语义与回归基线 | 验证减少中转的实际效果 |
| 当前进度 | 本地实现已完成初步设备与推理验证 | 设计与未集成原型，尚无完整直传推理结果 |

两者并非要求维护者在此阶段二选一。希望先确认合适的接口和上游边界，再按阶段提交。

### 2. 方案一：MemFabric L2 adapter

沿用 LMCache 的 store、lookup/lock、load、unlock、eviction 和 close 契约，通过 adapter 将 CPU `MemoryObj` 写入或恢复自 MemFabric BM 池。

首阶段采用单 service、固定成员、固定容量及易失索引；写入完成后才发布对象，读取保护期间不回收 slot。

这条路线尽量不改变现有 worker 搬运逻辑。其局限也需要保留在文档中：L2 使用了 MemFabric，不代表 NPU 与池之间已经消除了独立 CPU L1 阶段。

### 3. 方案二：worker 数据面与 service 控制面分离

建议的数据流为：

```text
P worker：真实 paged KV → 必要的 NPU gather 缓冲
                            ↓ MemFabric L2G
                          池 DRAM
                            ↓ MemFabric G2L
D worker：真实 paged KV ← 必要的 NPU scatter 缓冲

LMCache service：查询、空间分配、读写保护、提交、淘汰与失效处理
```

这里池的存储介质仍是 DRAM，attention 仍使用 NPU KV。目标是避免先落入另一份 CPU L1 payload 后再复制进池，不要求第一版消除所有 NPU 整理。

每个 worker 在自己的设备上下文中持有 BM client。service 向 worker 返回池对象描述符及使用凭证，而不是传递 KV payload。

描述符至少需要表达池会话、storage rank、offset/size、slot 代次和 KV layout。不能把其他进程的 CPU VA 当作通用地址，也不能只凭相同字节数判定布局兼容。

### 4. 复用已有 MP 扩展点

建议优先复用以下上游能力：

- [LMCache #3359](https://github.com/LMCache/LMCache/pull/3359)：worker 搬运、service 预留空间与 prepare/commit 生命周期。
- [LMCache #5159](https://github.com/LMCache/LMCache/pull/5159)：外部 MP server module 与控制面扩展。
- [LMCache #5805](https://github.com/LMCache/LMCache/pull/5805)：worker adapter、transfer context 等扩展钩子。

在已核对的固定 MP 基线上，可以从外部 Connector 的 `create_lmcache_mp_worker_adapter()` 和 WorkerAdapter 的 `_create_transfer_context()` 接入；service 使用现有 `--server-module`。

**不预设新增一套通用后端加载框架，也不以 core 零修改为约束。** scheduler 的查询、状态与锁语义需要单独映射；只有现有扩展点无法表达时，才提出最小 core 接口修改。

预计代码职责如下：

| 位置 | 预期职责 |
| --- | --- |
| LMCache-Ascend 的 L2 adapter / BM 封装 | 兼容路径及 MemFabric 会话、地址、搬运生命周期 |
| LMCache-Ascend 的外部 Connector / WorkerAdapter / TransferContext | 显式直传接入、NPU KV 整理和 L2G/G2L |
| LMCache-Ascend 的外部 service module | 池对象目录、slot、读写保护和提交状态 |
| LMCache core | 必要时补充现有 MP 无法表达的通用接口；具体缺口先讨论与验证 |
| Ascend native ops | 优先复用现有 block gather/scatter，确有布局或指针契约缺口再修改 |

### 5. 完成、取消与资源保护

建议保持以下基本顺序，具体 RPC schema 尚未定稿：

```text
写：预留对象 → gather → L2G 完成 → commit → 对象可读
读：查询并保护 → acquire → G2L → scatter 完成 → release
```

- 写完之前不能让 scheduler 或 D 将对象视为可用。
- scheduler 查询到 worker 真正读取之间需要保护；查询保护与 worker 的读取保护分别管理。
- `cleanup`、取消或 RPC 超时不代表 DMA 已停止，不能据此复用 slot 或解除映射。
- 迟到的 lookup 回包不能在请求结束后重新建立无法释放的保护。
- 缓存未命中可走正常重算；部分 scatter 后的错误应明确反馈到当前请求，不能冒充成功或默默继续。
- 首版固定成员失效后不自动重建；关闭时必须先排空可证明完成的操作。

第一版先使用同步 L2G/G2L，把正确性、完成通知和资源生命周期做清楚，再增加批量和传输重叠。

### 6. 首版范围与提交拆分

首版聚焦 Qwen3-8B、TP1/PP1/DCP1、标准单组 GQA、一个 service、单机固定成员。TP 扩展、多组/滑窗、MLA、KV events/CacheBlend、异步流水、动态重建及跨服务器性能另行推进。

建议拆分为：

1. 兼容 L2 adapter、配置、对象级检查和基础推理验收。
2. 利用现有 MP hooks 接入直传控制面与 worker 数据面，并完成 CPU 协议/生命周期测试。
3. 真实 NPU KV gather→池→scatter、严格 TP1 1P1D 验收及失败释放测试。
4. 在相同请求和明确的容量条件下补性能结果，再讨论批量、异步和 TP 扩展。

## 已考虑的替代方案

**直接让池内存成为 L1 的底层存储。** 这也可能消除独立 L1→池复制，并可参考 [LMCache #2647](https://github.com/LMCache/LMCache/pull/2647) 的外部共享池 allocator 思路。但该提案未合入，且 Ascend/MemFabric 的地址域、设备映射、所有权与多进程契约仍需验证，希望听取维护者对该落点的意见。

**通过 Mooncake Store/TransferEngine 间接接入 MemFabric。** 可以复用已有对象存储或传输实现，但会增加一层依赖与版本、ABI、allocator/注册适配。当前优先探索 LMCache 自身管理池对象的方案；这不是否定 Mooncake 路线。

**只优化兼容版的线程数、批量或异步。** 可以减少部分开销，但不能替代移除独立 CPU L1 payload 阶段的验证。因此希望保留这类优化与直传改造的区别。

## 补充信息与希望讨论的问题

### 已完成的先行验证

目前已在单机 Ascend A3 上进行以下验证：

1. **MemFabric 搬运原语**：两个独立 BM rank 之间，Host 和连续 NPU buffer 经 SDMA 写入对端 DRAM 后回读，数据一致。此项尚不包含真实 paged KV 的 gather/scatter。

2. **兼容版真实 1P1D**：Qwen3-8B 的 TP1、TP4 均完成受控 P→池→D 验收。放行 D 前确认 L1 为空；两种 TP 均实际回读 180 MiB，D 恢复 1280/1373 个输入 tokens，本地前缀命中为 0，答案正确。该结果确认 KV 来自池，但恢复仍经过临时 CPU L1。

3. **对象保护与异常路径**：已检查锁定对象不被淘汰、布局不匹配、延迟删除、请求取消及池成员退出等场景；池失效后，连续 24 条长输入请求正常重算完成。尚未覆盖任意 DMA 在途时刻的故障。

4. **直传控制面 CPU 原型**：完成 10 项纯 CPU 索引测试和 4 项 FakeSession 检查，覆盖提交前不可见、读保护、旧描述符拒绝、取消隔离等。它们不等价于真实 RPC、BM 或 NPU 联调。

上述结果为池存取和对象管理提供了初步依据，**真实推理 worker 的 NPU→池→NPU 直传仍未完成端到端验证**。目前也没有稳定性能提升结论，后续将分别验证数据路径正确性与端到端收益。

性能计划区分三组：LMCache 无 MemFabric、CPU L1 桥接、NPU 直传。先在相同池容量、请求和恢复量下比较后两组，再评估完整服务收益；总内存预留和有效容量分别报告。单机结果不外推跨服务器 HCCS。

### 兼容性

不改变现有 NPU 默认的 `lmcache_driven` 行为；直传显式选择。[LMCache #5555](https://github.com/LMCache/LMCache/issues/5555) 中默认模式与服务端能力对齐的考虑应继续保留。

现有 `engine_driven` 的 CPU/SHM 语义不能直接等同 MemFabric 直传。未选择直传时，不应引入额外的 MemFabric 初始化；选择直传但配置或能力不支持时，应明确报错，不能静默退回兼容路径后仍报告 direct 成功。

### 希望维护者讨论

1. 是否认可“兼容 L2 先落地、worker 直传作为可选后续路径”的拆分？两条路径适合放在 LMCache-Ascend 哪个模块边界？
2. 对直传，更推荐外部 TransferContext + service module，还是将池作为现有 allocator/storage location 的一种实现？是否有正在推进的相关工作可以复用？
3. scheduler 的查询、状态和读锁能否沿用现有协议？如果需要 core 改动，什么样的最小扩展更合适？
4. 上述同步 TP1 范围和完成/取消边界，是否适合作为首个可审阅实现？维护者希望优先看到哪些正确性与性能证据？
