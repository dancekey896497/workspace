# 给 NPU Agent：真实 DS 对象的 O_DIRECT 定位

**2026-10-16 执行顺序订正：机主不能直接操作，改用[本侧 fs_native 复现执行件](../fs_native本侧复现_20261016/00_给本侧NPUAgent_执行指令.md)，不再等待机主回传才能开始。本文保留为后续取得 DS 真实任务时的取证参考。**

## 结论与范围

**Qwen buffered 正配额实验已通过，不再重复。本轮优先捕获 DS 真实失败对象的地址和原始错误，再决定是否存在 PR305 未覆盖的问题。**

手工 ctypes 不对齐失败、mmap 对齐成功已经证明一种可能机制，但没有证明 DS 真实 L1 指针不齐。本地负责理论分析和补丁选择；你侧只做 NPU 取证和最小对照，不需要 GPU。

## 1. 先明确自己是哪一侧

- **能访问发生故障的 DS 服务：**优先执行下面真实任务取证。
- **只有 Qwen、没有 DS 模型或原 DS 环境：**不要下载大模型或把 Qwen 成功当 DS 验收。先让机主按本文回传真实任务信息；你可以用同版本、同 allocator 路径验证基本不变量，拿到 DS 实际布局后再决定是否需要无模型的小分配器复现。

只有保持真实分配器、A、分配来源和对象布局的 allocator/native 小实验，才可验证该路径的机制；不能用独立 ctypes buffer 冒充真实 L1。

## 2. 最优先：取一个真实失败任务

1. 记录实际加载的 LMCache/Ascend Python 路径、版本/commit 和 FS `.so` 路径。当前公开主仓标准 Lazy 已有基址对齐，现场无 Git 的副本直接检查文件内容。
2. 记录最终 allocator、`use_lazy`、`shm_name`、有效 `align_bytes`、实际 transfer mode。`auto` 只算配置值；找不到最终模式就写未核实。
3. 在专用测试进程临时增加以下两处日志，统一前缀 `[DIO-DIAG]`；只打印元数据，不打印 KV 内容，不改变指针、不拷贝数据、不提前释放对象。
4. 发一次 DS 的新前缀请求，足以产生至少一个完整 chunk；取得首个失败及少量相邻对象后先收束，不跑长压测。

### 临时日志插点

| 位置 | 必需内容 |
|---|---|
| `native_connector_l2_adapter.py::submit_store_task`，已生成最终 memviews、调用 native submit 的位置 | task/future 与 key 关联；真实 obj 类型、allocator、L1 owner；B、o、p、q、L、P、view.nbytes、A、D 和各余数 |
| 同文件 demux 的 store completion 分支 | task、future、ok、原始 `error`；当前只记通用 failed，必须保留 native 错误串 |

字段含义：

```text
B = 该对象所属 pool 的真实基址
o = obj.meta.address（当前标准 TensorMemoryAllocator 的池内偏移，不是指针）
p = obj.data_ptr
q = 最终提交 memoryview 的真实起点
L = obj.get_size()
P = obj.get_physical_size()
A = 有效 L1 align_bytes
D = connector 实际使用的 disk_block_size（本版来自 statvfs.f_bsize）
```

对象仍由真实 L1 reservation/读锁持有时观察。不要用 `bytes(view)`、`.clone()` 或新建 Tensor 后测地址，这会换掉被测缓冲。

若 final view 可写、连续且非空，可用 `ctypes.addressof(ctypes.c_ubyte.from_buffer(view))` 取 q；不满足条件就标未取得，不为诊断改变 view。池基址从对应真实 pool 记录，不能给不同 owner 的对象混用一个 B。

如果 Python 提交参数与错误仍不能闭环，再在 C++ `FSConnector::do_single_set`／`try_enable_odirect` 的等价位置，给错误补 key、最终 buf、len、D 和余数。此时才重建并核对实际加载的 `.so`，不要为了第一轮取证先重编整套依赖。

注意：地址 guard 在 open/write 前抛错，strace 没有对应失败 write 是预期；不能据此推翻 guard。反之，原始错误是实际 write EFAULT，就不要继续按“起始地址没对齐”直接定论。

## 3. 同 DS、单变量对照

**先拿真实错误；已有完整生产证据时，下一步才做配置对照。**

使用自己的测试实例与同文件系统上的新目录，不清原缓存、不使用历史服务 PID 直接结束进程。两组保持模型、TP、版本、allocator、A、传输模式和 eviction 策略一致，只改变 DIO。

| 组 | 条件 | 需要看到 |
|---|---|---|
| D1 | `use_odirect=true`，新 key | 首个真实错误、对象参数、是否实际开启 DIO |
| D2 | `use_odirect=false`，另一个新 key | 同 DS 路径的成功／失败、文件和用量 |

如果 D1 已有充分真实失败证据，可复用，不必再跑一遍。配置变化按现有可恢复流程重启自己的测试实例；不假定只重启 server 就能让旧客户端透明恢复。

隔离清理：可在专用目录去掉 eviction，且 D1/D2 都保持去掉；若保留 IsolatedLRU，先登记实际 salt 配额，确保本次数据低于水位。DS 不盲用 Qwen 的 8 GiB。

避免假通过：新前缀／新 key，不能走“文件已存在直接成功”的快路径；配置 DIO=true 也不证明真正使用 DIO，本版长度不满足 D 时会降级普通 I/O。

## 4. 判定后回传，不先写新 allocator

| 真实证据 | 应给出的判断 |
|---|---|
| 原始错误为地址 guard，最终 buf%D≠0 | DS 真实地址问题成立，再区分基址／偏移／view |
| B%D≠0，o%D=0，p=B+o | 池基址问题；核旧覆盖、旧 Lazy 或 non-lazy 分配分支 |
| B%D=0，o%D≠0 | 分配步长、A、group 或 subview 偏移问题 |
| p%D=0，但 q/native buf 不齐 | 中间包装层问题，不归因池基址 |
| 当前标准 Lazy 且 A=D=4096，却 p 不齐 | 实际版本／覆盖／owner 与源码保证矛盾，先对齐来源 |
| 地址、长度都齐，实际 write EFAULT | 转为映射、有效范围或生命周期问题；PR305 覆盖与否另核 |
| EINVAL | 核实际 DIO、地址／长度／offset 和 FS 要求，不直接等同 ACL |
| ENOSPC/EACCES/rename 等 | 按真实文件系统错误分流 |
| 只有合成 ctypes 错误，没有生产任务信息 | 仍未完成真实根因定位 |

首个新证据足以选择分支时先回传。本轮不自动增加对齐拷贝、改 `alloc_pinned_ptr`、round 原始 KV 指针、切换传输模式或重新执行全部旧内存矩阵。

## 5. 文本回传模板

```text
【结论先行】
生产DS真实地址未对齐：已证实 / 未证实 / 不具备DS环境
依据（必须关联真实task，不能只填合成probe）：

【实际环境】
模型名称/路径、TP、实际KV shapes/dtypes/object groups：
LMCache commit或版本 + 加载路径（无git则写明）：
Lazy构造是否有 overallocate + 对齐slice + base检查：
Ascend commit/等价PR305修改 + 加载路径：
FS .so 加载路径：
allocator类/来源函数、use_lazy、shm_name、有效A：
最终transfer mode及依据（auto不能代替最终模式）：
测试实例端口、真实base_path、FS类型、D：

【首个真实失败任务】
task_id / future_id / key / L1 owner：
对象类型、父allocator：
B / B%D：
o / o%D：
p / p%D / 是否等于B+o：
q / q%D（未取得则明确写）：
L / P / 最终view.nbytes / view.nbytes%D：
native buf / len / 实际DIO标志（未取得则明确写）：
completion.ok / 原始error全文：
相关[DIO-DIAG]日志原文：

【同DS对照】
DIO=true：新key、提交/成功/失败、文件数/bytes：
DIO=false：同项；没执行则写原因：
eviction禁用，或实际salt/配额/水位/最终usage：

【结尾】
是否只做了日志，是否有其他改动：
日志位置、测试服务保留/已停止：
未核实项：
GPU：本侧没有，未做GPU对照。
```
