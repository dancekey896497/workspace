# [RFC] 支持 MemFabric KV 池：兼容 L2 接入与 NPU worker 直传

## 问题

希望讨论在 LMCache-Ascend 的多进程（MP）模式下接入 MemFabric KV 池：保留 LMCache service 的缓存管理能力，由 MemFabric 提供池内存与数据搬运，不额外引入 MemCache 作为对象管理层。

考虑两条可选路径：

- **兼容 L2 路径**：通过 L2 adapter 接入 MemFabric，复用现有 CPU L1 和 NPU↔L1 搬运。
- **worker 直传路径**：worker 在自己的 NPU 上读写池，service 管理对象与完成状态，命中池时绕过独立 CPU L1 中转缓冲。

当前兼容原型的数据流为：

```text
写入：NPU KV → LMCache CPU L1 → MemFabric 池 DRAM
恢复：MemFabric 池 DRAM → LMCache CPU L1 → NPU KV
```

兼容路径可以先验证池存取与缓存语义，但仍保留独立 L1 缓冲和额外搬运。当 L1 已能满足前缀复用或 P/D 交接时，增加池可能只增加写入与维护成本。因此，池存取跑通与减少中转带来的性能收益，需要分别验证。

## 建议方案

先保留兼容路径作为回归基线，再提供显式选择的直传路径；确认接口与仓库边界后分阶段提交。

| 项目 | 兼容 L2 路径 | worker 直传路径 |
| --- | --- | --- |
| 缓存管理 | LMCache service | LMCache service |
| 池数据搬运 | service 的 L2 adapter：CPU L1↔池 | worker：NPU↔池 |
| 独立 CPU L1 中转 | 保留 | 命中池时绕过 |
| 当前进度 | 本地实现已完成初步设备与推理验证 | 设计与未集成原型，尚无完整直传推理结果 |

1. **兼容 L2 adapter。** 沿用 store、lookup/lock、load、unlock、eviction 和 close 契约，将 CPU `MemoryObj` 写入或恢复自 MemFabric BM 池。首版采用单 service、固定成员、固定容量及易失索引；写入完成后才发布对象，读取保护期间不回收 slot，尽量复用现有 worker 搬运逻辑。

2. **worker 数据面与 service 控制面。** 每个 worker 在自己的设备上下文中持有 BM client；service 负责查询、空间分配、读写保护、提交、淘汰与失效处理，只返回描述符和使用凭证，不传递 KV payload。直传路径为：

   ```text
   P：paged NPU KV → gather → NPU 缓冲 → L2G → 池 DRAM
   D：paged NPU KV ← scatter ← NPU 缓冲 ← G2L ← 池 DRAM
   ```

   池的存储介质仍是 DRAM，attention 仍使用 NPU KV；允许必要的 NPU gather/scatter 缓冲。描述符包含池会话、storage rank、offset/size、slot 代次和 KV layout。不能把其他进程的 CPU VA 当作通用地址，也不能只凭字节数判断布局兼容。

3. **复用现有 MP hooks。** 参考 [LMCache #3359](https://github.com/LMCache/LMCache/pull/3359) 的 worker 搬运与 prepare/commit 生命周期、[#5159](https://github.com/LMCache/LMCache/pull/5159) 的外部 server module，以及 [#5805](https://github.com/LMCache/LMCache/pull/5805) 的 worker adapter / transfer context 扩展点。在已核对的 MP 基线上，可由外部 Connector 重写 `create_lmcache_mp_worker_adapter()`，由 WorkerAdapter 重写 `_create_transfer_context()`；service 使用 `--server-module`。

   LMCache-Ascend 承担 L2 adapter、BM 封装、外部 Connector / WorkerAdapter / TransferContext 与 service module；优先复用现有 Ascend block gather/scatter。scheduler 的查询、状态与锁语义需单独映射，现有接口无法表达时再提出最小 core 修改，不预设新增通用后端加载框架。

4. **明确完成与取消语义。** 写入按“预留 → gather → L2G 完成 → commit → 可读”；读取按“查询并保护 → acquire → G2L → scatter 完成 → release”。查询保护与 worker 读保护分别管理，覆盖 scheduler 查询到实际读取之间的间隔；迟到的 lookup 回包不能在请求结束后重新建立无法释放的保护。

   `cleanup`、取消或 RPC 超时不代表 DMA 已停止，不能据此复用 slot 或解除映射。未命中可重算，部分 scatter 后的错误必须反馈到请求。首版使用同步 L2G/G2L，固定成员失效后不自动重建；关闭时排空操作，仅在确认完成后回收相关资源。具体 RPC schema 尚未定稿。

5. **限制首版范围并分阶段提交。** 聚焦 Qwen3-8B、TP1/PP1/DCP1、单个全注意力 KV cache group 与 GQA、一个 service、单机固定成员。TP 扩展、多组/滑窗、MLA、KV events/CacheBlend、批量与异步流水、动态重建及跨服务器性能后续另行推进。

   提交顺序建议为：兼容 L2 与基础推理验收；直传 hooks 接入及 CPU 协议/生命周期测试；真实 NPU gather→池→scatter、严格 TP1 1P1D 与失败释放验收；最后补相同请求和明确容量条件下的性能结果。

**已完成的先行验证**

设备与推理验证使用单机 Ascend A3；直传控制面原型另行进行了 CPU 测试：

1. **MemFabric 搬运原语**：两个独立 BM rank 之间，Host 和连续 NPU buffer 经 SDMA 写入对端 DRAM 后回读，数据一致。尚不包含真实 paged KV 的 gather/scatter。
2. **兼容版真实 1P1D**：Qwen3-8B 的 TP1、TP4 均完成受控 P→池→D 验收。放行 D 前确认 L1 为空；两种 TP 均实际回读 180 MiB，D 恢复 1373 个输入 tokens 中 1280 个的 KV，本地前缀命中为 0，答案正确。恢复仍经过临时 CPU L1。
3. **对象保护与异常路径**：覆盖锁定对象不被淘汰、布局不匹配、延迟删除、请求取消与池成员退出；池失效后，连续 24 条长输入请求正常重算完成。尚未覆盖任意 DMA 在途时刻的故障。
4. **直传控制面 CPU 原型**：10 项纯 CPU 索引测试和 4 项 `FakeSession` 检查通过，覆盖提交前不可见、读保护、旧描述符拒绝和取消隔离。它们不等价于真实 RPC、BM 或 NPU 联调。

**真实推理 worker 的 NPU→池→NPU 直传仍未完成端到端验证，目前也没有稳定性能提升结论。**

性能计划区分 LMCache 无 MemFabric、CPU L1 桥接、NPU 直传三组，先在相同池容量、请求和 KV 恢复量下比较后两组，再评估完整服务收益；总内存预留和有效容量分别报告，单机结果不外推跨服务器 HCCS。

## 已考虑的替代方案

- **让池内存成为 L1 的底层存储。** 可参考 [LMCache #2647](https://github.com/LMCache/LMCache/pull/2647) 的外部共享池 allocator 思路，以消除独立 L1→池复制。该提案未合入，Ascend/MemFabric 的地址域、设备映射、所有权与多进程契约仍需验证。
- **通过 Mooncake Store/TransferEngine 间接接入。** 可以复用已有对象存储或传输实现，但需增加依赖并处理版本、ABI、allocator 和内存注册适配；当前优先探索由 LMCache 管理池对象。
- **只优化兼容版线程数、批量或异步。** 可以减少部分开销，但不能替代绕过独立 CPU L1 缓冲的验证，这两类改动应分别评估。

## 兼容性

- 不改变现有 NPU 默认的 `lmcache_driven` 行为；直传显式选择。[LMCache #5555](https://github.com/LMCache/LMCache/issues/5555) 中默认模式与服务端能力对齐的考虑应继续保留。
- 现有 `engine_driven` 的 CPU/SHM 语义不能直接等同 MemFabric 直传。未选择直传时，不引入额外的 MemFabric 初始化；选择直传但配置或能力不支持时明确报错，不能静默退回兼容路径后仍报告 direct 成功。

## 希望社区讨论的问题

1. 是否认可“兼容 L2 先落地、worker 直传作为可选后续路径”的拆分？两条路径适合放在 LMCache-Ascend 哪个模块边界？
2. 对直传，更推荐外部 TransferContext + service module，还是将池作为现有 allocator/storage location 的一种实现？是否有正在推进的相关工作可以复用？
3. scheduler 的查询、状态和读锁能否沿用现有协议？如果需要 core 改动，什么样的最小扩展更合适？
4. 上述同步 TP1 范围和完成/取消边界，是否适合作为首个可审阅实现？维护者希望优先看到哪些正确性与性能证据？
