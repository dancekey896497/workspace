# 保留 fs_native + O_DIRECT，在我们自己的 NPU 环境复现

日期：2026-10-16。本文取代上一轮“等待机主提供 DS 原始任务”的执行顺序；机主暂时不能操作，不再把机主回传作为启动条件。

## 结论与任务

**结论：先用真实 L1 分配器和 native FS client 复现，再用真实 MP server 验证完整链路。保留 `fs_native` 与 `use_odirect=true`；不重新论证已经现场通过的 PR305，不先下载 DS 大模型。**

背景：Qwen 正配额实验已经通过，但那组关闭了 O_DIRECT。机主的合成非对齐样本证明 native FS 会拒绝不齐的地址，尚未证明其真实 DS 对象也是这个问题。我们现在只依赖自己的 NPU 环境，建立可重复的小场景。

本机 Windows 仅核过源码、准备脚本；以下 NPU 实验**未执行、未核实**。请只在自己的测试容器/进程执行，保留结果，先不要写修复补丁。

## 1. 固定本侧环境，不要求机主补材料

**结论：沿用本侧已工作的 NPU 软件栈，记录实际版本；“按文档 pull”不能代替版本号，但也不是阻塞项。**

1. 记录当前镜像 tag/ID、LMCache/Ascend 的 commit 或安装版本、Python 导入路径、`lmcache.lmcache_fs` 的 `.so` 路径、SSD 挂载和文件系统类型。
2. 确认本侧包含 #305 的等价删除，并确认实际 Lazy 使用主仓实现。主仓 Lazy 应包含“超额分配 + 对齐切片 + 基址检查”；缺少时先报告，不能默认为存在。
3. 不升级 CANN、驱动、PyTorch、vLLM 或依赖，不覆盖已有安装。脚本 API 按主仓 `ddcbe3ed01b9cdab765271bd98de0b9d9e3c4ced` 核对，Ascend 参考 `b813e970075983e94e86cc2911a40b1c00e7c780`；这两个 SHA 是分析参照，不是要求把现场升级到它们。
4. 先看现场 `lmcache server --help` 和 `lmcache bench server --help`。若缺参数/API，回传具体差异，在现有版本做等价适配，不安装最新版来掩盖差异。

当前 Ascend 文档的 main 安装是滚动分支，MP 示例默认 L1；`fs_native` 是额外添加的主仓 L2 适配器。机主按文档安装不等于配置错误，也不保证与你我今天拉到同一提交。[安装说明](https://github.com/LMCache/LMCache-Ascend/blob/b813e970075983e94e86cc2911a40b1c00e7c780/README.md)

## 2. 第一层：真实 L1 → native FS，不用模型

**结论：先执行随附脚本；它测真实池对象，不使用手工 ctypes 分配替代 L1。**

将 [probe_l1_fs_native.py](probe_l1_fs_native.py) 放入测试容器。下面是 Linux Bash 示例；先把 SSD 目录改成自己实际挂载下的专用目录：

```bash
export FS_REPRO_PARENT=/ssd/lmcache/_our_fs_native_repro
mkdir -p "$FS_REPRO_PARENT"
findmnt -T "$FS_REPRO_PARENT"
python probe_l1_fs_native.py --base-path "$FS_REPRO_PARENT" --device npu
```

脚本在该目录自动新建独立子目录，不删除缓存。默认只分配 64 MiB L1，不扩容；按文件系统块大小 D，测试 `4D` 和 `4D-1` 两种逻辑长度，每种 4 个对象，逐对象写入、读回、比较内容。

它记录真实池基址 B、池内偏移 o、对象地址 p、最终 view 地址 q、逻辑长度 L、物理长度 P、对齐 A、文件系统块大小 D，以及 native completion 原始错误。写入用原 L1 view，校验时才复制成 Python bytes；该复制不参与 I/O。

它没有执行 NPU↔CPU 数据搬运，也没有经过完整 MP adapter；普通 CPU 对象的 `fill_` 不等于 NPU DMA 验收。脚本保持对象直到 native worker 关闭后再释放；超时后若关闭阻塞，保留现场，结束的只能是自己的测试进程，不能在 worker 活跃时手动 free 对象。

| 脚本结果 | 判定与动作 |
|---|---|
| `COMPONENT_CHECK_PASSED` | 该真实 L1/native FS 小场景写读正确，参数满足本版 DIO 条件；继续第二层。未做 syscall trace 时，实际系统调用标志仍写未核实 |
| `STORE_FAILED`，error 明确地址未对齐 | 已在本侧真实 L1 对象复现地址 guard；回传该对象全部字段，按下文判定，不继续堆请求 |
| `READBACK_FAILED` | 回传 load error、命中位和读回比较；不能把它概括为“L2 没东西” |
| `READBACK_OK_DIO_PRECONDITIONS_NOT_MET` | 可能走普通 I/O，不能算 O_DIRECT 通过 |
| `SKIP_LAZY_AUTO_DISABLED` | 没进入目标 Lazy 路径，先核设备识别/插件加载，不能把跳过当通过 |
| 导入、配置参数、内存注册或扩展加载报错 | 环境/API 或更早阶段失败，回传错误，不能称作已复现 FS 写入根因 |

不默认跑 `nonlazy-native`。只有本侧启动日志证明目标走 non-lazy 且未用 SHM/NUMA/DAX 时，才追加 `--allocator nonlazy-native`；它是另一条分配路径，失败不能用于否定 #305。

## 3. 第二层：真实 MP server → fs_native，不用 DS

**结论：使用主仓自带的 server bench 产生少量 NPU KV，验证真实传输和 L2 adapter；不必先起大模型或填满 L1。**

先检查 15555/18080 未被占用；若占用，为自己的实例换一对端口，同时修改客户端。第一轮只隔离 L2 驱逐：不配 JSON 的 `eviction`，避免写成功又被零配额清走；`fs_native + use_odirect=true` 始终保留。第二层不是原始全部启动参数的最终验收。

在一个终端前台运行，便于只停止自己的进程：

```bash
export FS_MP_DIR=$(mktemp -d "$FS_REPRO_PARENT/mp_XXXXXXXX")
export FS_MP_ADAPTER=$(python -c 'import json,os; print(json.dumps({"type":"fs_native","base_path":os.environ["FS_MP_DIR"],"num_workers":4,"use_odirect":True}))')
printf '%s\n' "$FS_MP_DIR"
LMCACHE_LOG_LEVEL=DEBUG lmcache server \
  --host 127.0.0.1 --port 15555 \
  --http-host 127.0.0.1 --http-port 18080 \
  --chunk-size 4096 --supported-transfer-mode lmcache_driven \
  --l1-size-gb 1 --l1-init-size-gb 1 \
  --l1-use-lazy --l1-align-bytes 4096 \
  --eviction-policy LRU --worker-reap-timeout-seconds 0 \
  --l2-store-policy default \
  --l2-adapter "$FS_MP_ADAPTER" \
  2>&1 | tee "$FS_MP_DIR/server.log"
```

另一个终端在相同测试环境运行：

```bash
lmcache bench server \
  --rpc-url tcp://127.0.0.1:15555 \
  --url http://127.0.0.1:18080 \
  --mode gpu --transfer-mode lmcache_driven \
  --tp-size 1 --num-tokens 8192 \
  --kvcache-shape-spec '(2,512,16,8,128):float16:2' \
  --start 41001 --end 41002 \
  --interval 0.5 --concurrency 1
```

`gpu` 是该 CLI 的模式名，实际设备由 `torch_device_type` 决定。必须见到类似 `[rank 0] Allocated 2 KV tensors on npu:0`；若实际为 CPU/CUDA，停止该层验收。`--url` 是 LMCache HTTP 地址，不是 vLLM 地址；开启认证的环境要核 bench 客户端支持情况，401 不属于 FS 错误。

这个输入模拟两层普通 K/V，总计约 64 MiB NPU KV；不是 DS/MLA 布局。`[41001,41002)` 只跑一个序列，8192 token 对应两个完整 chunk。重跑使用新编号，例如 `[41003,41004)`，并核对生成了新 key，避免缓存命中绕过写入。

若现场版本没有这个 bench，沿用本侧已经接通 LMCache 的 Qwen 测试客户端发新前缀请求；不得把没有 LMCache connector 的 vLLM 成功当作本实验成功。

### 第二层如何判定

**结论：需要“新 key 的 L2 完成 + 新文件/用量”同时成立；客户端成功或 L1 命中都不够。**

| 观察 | 判断 |
|---|---|
| `L2 store task ... completed: adapter ..., ... keys.`，并出现本轮新缓存文件，等待后持续保留 | 实际 MP/native L2 store 成功。日志数与文件数需等待异步收敛，不要求同一瞬间一致 |
| `L2 store task ... failed` | 取 native completion 的原始 error，不继续猜 quota/模型 |
| 根本没有 L2 store 提交 | 先核客户端连接、真实 adapter、store policy、完整 chunk；还没进入 DIO 诊断 |
| L2 写成功后有 eviction/delete，文件随后消失 | 清理分支，不是同一条写失败证据 |
| bench checksum/warm hit 成功但无 L2 证据 | 可能只命中 L1，L2 未验收 |

文件统计只数本版缓存最终 `.data` 文件，排除本文保存的 `.log`、JSON 和临时文件；现场后缀不同就按实际 key 到路径的规则统计，不硬套名称。默认 store policy 会把新完成的 L1 对象交给 L2，不需要先用满 1 GiB 或 512 GiB 才“驱逐出来”。[store policy](https://github.com/LMCache/LMCache/blob/ddcbe3ed01b9cdab765271bd98de0b9d9e3c4ced/lmcache/v1/distributed/storage_controllers/store_policy.py#L113)

若只有通用 failed：在自己测试副本 `native_connector_l2_adapter.py` 的 store completion 分支，给原有失败日志追加已经收到的 `error`；同步记录 task/future/key。必要时在生成最终 memviews 后记录脚本同款 B/o/p/q/L/P/A/D。日志不修改指针，不创建替代 I/O 缓冲。旧版具体插点按实际代码找，不盲套行号。[参考分支](https://github.com/LMCache/LMCache/blob/ddcbe3ed01b9cdab765271bd98de0b9d9e3c4ced/lmcache/v1/distributed/l2_adapters/native_connector_l2_adapter.py#L509)

## 4. 根据结果，只补一个最有价值的实验

**结论：前两层通过后，不跑一大套盲矩阵；只针对尚未覆盖的真实差异补测。**

| 当前结果 | 下一步 |
|---|---|
| 地址 guard；B%D≠0、o%D=0、p=B+o | 核实际 Lazy 来源/旧覆盖/非 Lazy 分支，修池的来源点 |
| B%D=0、o%D≠0 | 核 A 与槽位/group 步长；不是 #305 的基址替换能直接解决 |
| p 齐而最终 q 不齐 | 核 view/subview 包装层 |
| 地址和长度都齐，实际 write 报 EFAULT | 分析映射、可访问范围、异步所有权；先不要追加“对齐补丁” |
| EINVAL/ENOSPC/EACCES 等 | 依错误核实际 DIO 条件或文件系统，不先归因 ACL |
| 组件通过、真实 MP 失败 | 重点比较真实传输后的对象、分配分支、生命周期，保留首个任务原始错误 |
| 两层都通过 | 本侧关键小链路正常，原 DS 仍未复现。恢复原 IsolatedLRU + 足够的实际 salt 正配额，确认写后不清理；不要重做已通过的 Qwen buffered 配额实验 |

若需要恢复 IsolatedLRU，沿用前一轮已经核过的配额 API/流程，**在流量前**写入实际 salt 的正配额；预算须高于本轮物理数据量除以水位，不能把某个模型的 8 GiB 当通用值。若连 L2 eviction 也不允许临时变化，第二层直接保留策略并先配置足够配额，记录预算和最终 usage。

仍不复现时，优先在本侧同栈尝试能获得的 DS 实际 KV layout；没有 DS 模型也可构造对应 shapes/dtypes/groups 的测试输入。当前不知道确切 DS 布局，不能用猜测的 MLA 维度冒充。只有出现“扩大 L1 后才失败”等信号才测扩容/容量；64 MiB/1 GiB 通过不覆盖原 512 GiB 规模。

不要为找到一个失败而改用旧分配器、故意降低 A，随后声称复现了机主；这样的实验只能说明机制。

## 5. 只回传下面文本

```text
【结论】
组件：通过 / 真实L1写失败 / 读回失败 / 环境阻塞
MP：通过 / store失败 / 未提交L2 / 写后被清理 / 未执行
与机主原DS：本侧复现了同类错误 / 仅机制吻合 / 仍未复现

【固定环境】
镜像tag与ID；LMCache/Ascend版本和加载路径；FS .so路径：
PR305等价删除；主仓Lazy基址对齐代码是否存在：
allocator类和来源；use_lazy/shm_name/A；FS类型/D：
最终传输模式及依据；GPU对照：无GPU，未执行

【组件首个有代表性的对象】
脚本status；result.json路径：
B/B%D，o/o%D，p/p%D，q/q%D：
L/P/view.nbytes/itemsize，p=B+o?，p=q?：
store.ok/error；load.ok/error/result_bools；readback_equal：
实际O_DIRECT系统调用标志：已确认(写依据) / 未核实

【真实MP】
启动参数(含fs_native/DIO/eviction)；实际base_path：
NPU分配日志；本轮新seq/key；L2提交/完成/失败日志原文：
native原始error(无则写无)；必要时真实任务B/o/p/q/L/P/A/D：
缓存最终文件后缀/数量/总bytes，观察时间点；是否有delete/eviction：
如保留IsolatedLRU：实际salt、quota、usage、水位：

【结束】
只新增了哪些诊断；未改哪些环境/代码：
未核实：DS布局/512GiB规模/冷L1首次L2 load/其他：
自己的服务和测试目录是否保留，日志路径：
```

第一层已能明确失败分支时先回传，不必把后面所有步骤跑完。冷 L1 的首次 L2 load 属于后续读取验收；本轮优先回答“是否写进 L2”，不能用一次 warm hit 代替它。
