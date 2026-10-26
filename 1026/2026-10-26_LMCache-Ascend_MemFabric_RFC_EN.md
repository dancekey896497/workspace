# [RFC] MemFabric-backed KV pooling: L2 integration and direct NPU worker transfers

## Problem

We would like feedback on adding a MemFabric-backed KV pool to LMCache-Ascend in multi-process (MP) mode. LMCache service would retain cache management, while MemFabric provides pool memory and data movement, without introducing MemCache as another object management layer.

We are considering two paths:

- **L2 compatibility path:** integrate through an L2 adapter, reusing CPU L1 and existing NPU-to-L1 transfers.
- **Direct worker path:** workers transfer KV between their NPU and the pool, while the service manages objects and completion state. Pool hits bypass a separate CPU L1 staging buffer.

The compatibility prototype currently follows:

```text
Store:   NPU KV -> LMCache CPU L1 -> MemFabric pool DRAM
Restore: MemFabric pool DRAM -> LMCache CPU L1 -> NPU KV
```

This validates pool access and cache semantics, but retains intermediate staging. When L1 already handles prefix reuse or the prefill/decode handoff, adding a pool may add writes and bookkeeping without serving pool reads. Pool access alone therefore does not demonstrate the benefit of removing that staging.

## Proposal

Keep the compatibility path as a regression baseline, then add direct transfers as an explicit opt-in. We would first agree on interfaces and repository boundaries, then submit the work in stages.

| Aspect | L2 compatibility path | Direct worker path |
| --- | --- | --- |
| Cache management | LMCache service | LMCache service |
| Pool transfers | Service L2 adapter: CPU L1 to/from pool | Worker: NPU to/from pool |
| CPU staging | Separate L1 buffer retained | Bypassed for pool hits |
| Status | Local prototype with initial device and inference validation | Design and unintegrated prototype; no end-to-end direct inference result |

1. **Preserve the L2 contract.** The adapter stores CPU `MemoryObj` data in a MemFabric BM pool and follows existing store, lookup/lock, load, unlock, eviction, and close semantics. Initially it uses one service, fixed membership and capacity, and an in-memory index. Objects become visible after writes complete; protected slots cannot be reclaimed. Existing worker transfers remain largely unchanged.

2. **Move direct transfers into workers.** Each worker owns a BM client in its device context. The service handles lookup, allocation, protection, commit, eviction, and invalidation, returning descriptors and access information rather than KV payloads. The data path is:

   ```text
   P: paged NPU KV -> gather -> NPU buffer -> L2G -> pool DRAM
   D: paged NPU KV <- scatter <- NPU buffer <- G2L <- pool DRAM
   ```

   Pool storage remains DRAM, attention uses NPU KV, and necessary NPU gather/scatter buffers remain allowed. Descriptors identify the pool session, storage rank, offset/size, slot generation, and KV layout. Another process's CPU virtual address is not portable; equal byte counts do not establish layout compatibility.

3. **Reuse MP hooks and keep responsibilities clear.** Build on [LMCache #3359](https://github.com/LMCache/LMCache/pull/3359) for prepare/commit and worker transfers, [#5159](https://github.com/LMCache/LMCache/pull/5159) for external server modules, and [#5805](https://github.com/LMCache/LMCache/pull/5805) for worker-adapter and transfer-context hooks. On the inspected baseline, an external connector can override `create_lmcache_mp_worker_adapter()`, and its worker adapter can override `_create_transfer_context()`. The service uses `--server-module`.

   LMCache-Ascend would own the L2 adapter, BM wrapper, connector, worker adapter, transfer context, and pool-control module. Reuse native block gather/scatter where possible. Scheduler lookup, status, and locking still need mapping; propose a small core hook only if a concrete gap remains. A new generic backend loader is not assumed.

4. **Make completion and cancellation explicit.** Store follows `reserve -> gather -> L2G completion -> commit -> visible`; load follows `lookup/pin -> acquire -> G2L -> scatter completion -> release`. Lookup pins protect the interval before a worker reads; worker read leases are managed separately. Cancellation, cleanup, and RPC timeouts do not prove DMA completion and cannot alone permit slot reuse or unmapping. Late lookup responses must not recreate unreleasable pins. Misses may recompute; partial-scatter failures must reach the request rather than appear successful. Start with synchronous transfers and no automatic group rebuild after member failure. Shutdown must drain operations and reclaim resources only when completion is known. The RPC schema remains open for discussion.

5. **Keep the first implementation narrow and submit in stages.** Target Qwen3-8B, TP1/PP1/DCP1, one full-attention GQA KV group, one service, and fixed membership on a single host. First submit the compatibility adapter and basic inference checks; then direct worker/control-plane integration with CPU lifecycle tests; then real gather-to-pool-to-scatter, controlled TP1 1P1D, and failure/cleanup validation; finally performance results. TP expansion, multiple groups, sliding windows, MLA, KV events/CacheBlend, batching, asynchronous pipelines, dynamic recovery, and cross-server performance remain outside this scope.

**Preliminary validation**

Device and inference checks used a single Ascend A3 server; the control-plane prototype was tested separately on CPU.

1. **Transfer primitives:** two independent BM ranks wrote host and contiguous NPU buffers to peer DRAM over SDMA and read them back with matching data. This did not exercise real paged-KV gather/scatter.
2. **Compatibility-path 1P1D:** Qwen3-8B passed controlled P-to-pool-to-D tests at TP1 and TP4. L1 was empty before D proceeded. Each test recorded 180 MiB of pool reads; D restored KV for 1,280 of 1,373 prompt tokens, with zero local prefix-cache hits and a correct answer. Restoration still used temporary CPU L1.
3. **Protection and failure paths:** checks covered locked-object eviction protection, layout mismatches, deferred deletion, cancellation, and member exit. After pool failure, 24 consecutive long-input requests completed correctly through recomputation. Failures at arbitrary points during in-flight DMA remain unverified.
4. **Direct control-plane prototype:** 10 CPU-only index tests and 4 `FakeSession` checks passed, covering invisibility before commit, read protection, stale descriptors, and cancellation isolation. These do not validate real RPC, BM, or NPU integration.

**Real inference-worker NPU-to-pool-to-NPU transfers have not been validated end to end, and no consistent performance improvement has been established.**

Performance testing would compare LMCache without MemFabric, MemFabric through CPU L1, and direct NPU transfers. First compare the latter two with matched pool capacity, requests, and KV restored, then assess complete-service performance. Report total memory reserved and usable cache capacity separately; do not extrapolate single-host results to cross-server HCCS.

## Alternatives considered

- **Back L1 with pool memory.** This could remove the separate L1-to-pool copy. [LMCache #2647](https://github.com/LMCache/LMCache/pull/2647) explored an external shared-pool allocator but was not merged. Ascend/MemFabric address spaces, device mappings, ownership, and multi-process behavior would still need validation.
- **Use Mooncake Store or TransferEngine.** This could reuse existing storage or transfer implementations, but adds dependencies and work on version/ABI compatibility, allocators, and registration. We are currently exploring LMCache-managed pool objects while keeping this alternative open.
- **Only optimize the compatibility path.** Threading, batching, and overlap may reduce overhead, but do not establish whether bypassing separate CPU L1 staging helps. We would evaluate these separately.

## Compatibility

- Keep the existing NPU default, `lmcache_driven`, unchanged; direct transfers require opt-in. The reasoning in [LMCache #5555](https://github.com/LMCache/LMCache/issues/5555) about aligning defaults with server capabilities still applies.
- Existing `engine_driven` CPU/SHM semantics do not automatically provide MemFabric direct transfers. When direct mode is not selected, this work should add no MemFabric initialization. Unsupported direct configurations must fail explicitly, rather than silently use the compatibility path and report direct success.

## Questions for the community

1. Does landing compatibility L2 first, with optional direct transfers later, make sense? Where should these components live in LMCache-Ascend?
2. Would you prefer external transfer contexts plus a service module, or an existing allocator/storage location backed by pool memory? Is related work already underway?
3. Can scheduler lookup, status, and read protection reuse existing protocols? If a core change is needed, what is the smallest useful extension?
4. Is the synchronous TP1 scope and its completion/cancellation model suitable for a first implementation? Which correctness and performance results should come first?
