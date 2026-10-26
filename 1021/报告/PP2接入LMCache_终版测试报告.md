# PP＝2接入LMCache验证报告：客户数据集OFF／ON对照

整理日期：2026年10月26日。结果仅采用**2026年10月25日客户数据集补采**，对照为 **LMCache OFF → LMCache ON（原异步）**。

## 一、结论

在相同P／D计算形态、相同389条客户原文和相同输出长度下，接入LMCache后，本轮平均TTFT下降**19.73%**，平均TPOT下降**1.07%**，平均端到端时延下降**3.59%**，输入与输出吞吐均提高**3.73%**。两组均389成功、0失败。

主要收益在P侧处理输入：P平均Prefill阶段由1084.374ms降至672.366ms。中位TTFT仅下降0.31%，P90 TTFT下降26.64%，说明改善在不同请求之间并不均匀。D侧算法与参数未改，本轮TPOT的小幅变化不能直接解释为LMCache加速了D的每一步计算。

这是本轮已完成的单轮对照结果，不作多轮稳定收益保证。本次仅整理本地归档，没有新增压测、改源码或启停服务。

## 二、验证条件和客户数据

| 项目 | 两组共同条件 |
| --- | --- |
| 结果日期 | 2026年10月25日补采 |
| 机器／容器 | P：8／ks-pp-luv-m8-priv；D与代理、压测：12／ks-pp-m12-priv |
| 模型 | GLM-5.1-w8a8c8-MTP，ascend量化；容器内路径 /mnt/sdb/models/GLM-5.1-w8a8c8-MTP |
| P计算形态 | DP1／TP8／PP2，前41层＋后37层；DSA CP=true；MTP3；EP开启 |
| D计算形态 | DP4／TP4／PP1，四接口；MTP3；EP开启；两组保留同一批D模型进程 |
| P容量与执行 | max-model-len=49152；util=0.94；batch token=16384；max-seqs=64；eager；APC关闭 |
| D容量与执行 | max-model-len=65536；显式KV池4831838208字节（4.5GiB）；util=0.92；batch token=164；max-seqs=32；FULL_DECODE_ONLY；async-scheduling；APC关闭 |
| 数据来源 | 客户DS数据集原文，按GLM tokenizer重新计数；按输入＋原输出不超过49152筛选，保留原文和原序，不截断或重写 |
| 覆盖 | 原500条中的389条，覆盖77.8%；未纳入的111条不计入结论 |
| 正式请求／并发／预热 | 389条／4／0；request-rate=inf；disable-shuffle；seed=0；temperature=0；ignore-eos |
| 输入token | 合计3,907,699；均值10,045.499；中位3969；范围261—48030 |
| 输出token | 逐条保留原目标（custom-output-len=-1），不是每条固定500；实际合计191,207，均值491.535，中位470，范围100—891 |
| 可比性核对 | 同一数据池按原序逐条对齐，input_lens与output_lens完全一致；不代表生成文字完全相同 |
| 缓存与进程经历 | 每组正式轮前做1条最长容量诊断；ON正式轮前清空并验空LMCache，随后随正式请求自然积累，不额外预热；模型进程热度未单独隔离 |
| 统计范围 | 两组均389成功／0失败；389条全部纳入，没有剔除长尾 |

### OFF与ON的接入差异

| 项目 | LMCache OFF | LMCache ON（原异步） |
| --- | --- | --- |
| P Connector | MooncakeConnectorV1 | MultiConnector：MooncakeConnectorV1＋LMCacheMPConnector |
| LMCache范围 | P不使用LMCache | 只用于P侧历史KV缓存；本机MP缓存服务 |
| P→D传输 | Mooncake Ascend Direct | 相同Mooncake Ascend Direct |
| D Connector | MooncakeConnectorV1 consumer | 相同；D不直接接入LMCache |
| 源码选择 | 共用baseline私有环境 | 同一baseline；使用既有17行握手／STORE适配、pin及共同观测 |
| 计算与压测参数 | 上表全部共同参数 | 相同 |

本报告的ON就是原异步接入，不包含另外的恢复／提前读取优化开关。已有17行适配、pin和共同观测属于实测环境依赖，复现时不能把它们悄悄替换为未适配的干净安装。

## 三、验证结果

### 3.1 客户端性能对比

时间越低越好，吞吐越高越好。TTFT是发出请求到首个输出；TPOT是每条请求首输出后的耗时除以实际输出token数减1，再按请求统计；端到端时延是发出请求到整条输出结束。P90／P95／P99是对应分位门槛。

| 指标 | LMCache OFF | LMCache ON（原异步） | OFF→ON变化 |
| --- | --- | --- | --- |
| **平均TTFT（ms）** | 1,750.574 | 1,405.219 | 下降19.73%（改善） |
| **平均TPOT（ms/token）** | 23.158 | 22.910 | 下降1.07%（改善） |
| **平均端到端时延（ms）** | 13,002.568 | 12,536.321 | 下降3.59%（改善） |
| **输入吞吐（token/s）** | 3,068.623 | 3,183.010 | 提高3.73%（改善） |
| **输出吞吐（token/s）** | 150.150 | 155.747 | 提高3.73%（改善） |
| 请求吞吐（req/s） | 0.305472 | 0.316859 | 提高3.73%（改善） |
| 整轮压测时长（s） | 1,273.437 | 1,227.674 | 下降3.59%（改善） |
| 中位TTFT（ms） | 1,267.545 | 1,263.568 | 下降0.31%（改善） |
| 中位TPOT（ms/token） | 22.854 | 22.576 | 下降1.22%（改善） |
| 中位端到端时延（ms） | 12,470.839 | 12,056.585 | 下降3.32%（改善） |
| P90 TTFT（ms） | 3,073.435 | 2,254.785 | 下降26.64%（改善） |
| P95 TTFT（ms） | 3,878.420 | 2,669.463 | 下降31.17%（改善） |
| P99 TTFT（ms） | 4,940.026 | 3,133.066 | 下降36.58%（改善） |
| P90 TPOT（ms/token） | 27.151 | 26.454 | 下降2.57%（改善） |
| P95 TPOT（ms/token） | 28.187 | 27.940 | 下降0.88%（改善） |
| P99 TPOT（ms/token） | 30.231 | 29.759 | 下降1.56%（改善） |
| P90端到端时延（ms） | 20,251.614 | 19,714.795 | 下降2.65%（改善） |
| P95端到端时延（ms） | 21,523.972 | 20,705.900 | 下降3.80%（改善） |
| P99端到端时延（ms） | 23,824.036 | 23,458.507 | 下降1.53%（改善） |
| 最大TTFT（ms） | 6,788.728 | 4,288.175 | 下降36.83%（改善） |
| 最大TPOT（ms/token） | 31.812 | 32.692 | 增加2.77%（略慢） |
| 最大端到端时延（ms） | 26,475.580 | 26,135.280 | 下降1.29%（改善） |
| TTFT标准差（ms） | 1,014.476 | 559.570 | 下降44.84%（改善） |
| TPOT标准差（ms/token） | 2.788 | 2.693 | 下降3.39%（改善） |
| 端到端时延标准差（ms） | 5,464.522 | 5,320.779 | 下降2.63%（改善） |

- 输入吞吐按“输入总token÷整轮时长”派生，包含缓存复用的逻辑输入，不代表设备实际计算吞吐；输出吞吐按实际输出token÷整轮时长。
- 分位数、均值和标准差采用官方benchmark导出；最大值从保存的逐请求数组派生。百分比由未舍入值计算。
- 全量389条都已计入；没有通过剔除较慢请求改善表中结果。最大TPOT增加2.77%也按原值保留。

### 3.2 服务端阶段对照

下表用于观察收益发生在哪一侧；服务端统计与客户端TTFT、TPOT的测量边界不同，不能直接拼接相加，也不是设备纯算子计时。

| 服务端指标 | LMCache OFF | LMCache ON（原异步） | OFF→ON变化 |
| --- | --- | --- | --- |
| P平均TTFT（ms） | 1,266.452 | 928.290 | 下降26.70%（改善） |
| P平均Prefill阶段（ms） | 1,084.374 | 672.366 | 下降38.00%（改善） |
| D平均TTFT（ms） | 448.091 | 440.105 | 下降1.78%（改善） |
| D平均Prefill阶段（ms） | 121.348 | 121.558 | 增加0.17%（略慢） |
| D平均Decode阶段（ms） | 11,263.047 | 11,141.836 | 下降1.08%（改善） |
| D平均TPOT（ms/token） | 23.188 | 22.939 | 下降1.07%（改善） |

D的MTP接受率由63.991%变为65.105%，增加1.114个百分点。它与D Decode／TPOT变化同向，但生成轨迹、请求到达及批次组合没有被分别隔离，不能据此断言唯一原因。

## 四、与客户原始脚本的关系

**本次不是“除了2P1D之外全部相同”。** 下表明确区分客户原始部署与本项目的共同适配；这些共同变化在本报告OFF／ON两组之间保持不变。

| 项目 | 客户原始GLM脚本 | 本次OFF与ON的共同设置 |
| --- | --- | --- |
| P部署 | 两个P节点，nnodes=2、node-rank=0／1、TP16／PP2 | 单P节点，nnodes=1、node-rank=0、TP8／PP2；1P1D |
| PP分层／DSA CP／MTP | 41,37／true／3 | 保持相同 |
| P最大长度／util | 202752／0.92 | 49152／0.94，用于本次可运行的客户原文子集 |
| D最大长度／KV池 | 202752／未显式指定池字节 | 65536／显式4.5GiB |
| vLLM本地前缀缓存（APC） | P显式开启；D未显式指定 | P、D均显式关闭；OFF/ON共同条件 |
| P的NPU分配器 | expandable_segments:True | expandable_segments:False；OFF/ON共同条件 |
| 批次与主要执行参数 | P batch16384/max-seqs64/eager；D batch164/max-seqs32/FULL_DECODE_ONLY/async；主要additional-config | 沿用，完整参数见下方两组命令 |
| 路径、网络与传输身份 | 客户模型路径、主节点地址、7060主端口、KV端口30000/30200及原engine_id | 现有容器模型路径、8/12内网地址、18190主端口、KV端口28000/29000及本实验engine_id；peer布局同步匹配 |
| 压测 | 客户DS原文和参考压测方式；原参考规模不是本次GLM实测口径 | 同一客户原文的389条GLM容量子集、C4、预热0、逐条原输出，采用当前版本vllm bench serve |
| 定位观测与接入适配 | 客户原脚本未包含本项目定位配置 | 保留既有私有baseline、trace及profiler入口；ON增加LMCache接入。不是纯官方无补丁对照 |

客户原件：客户原始GLM脚本说明（`../../../06_客户资料与历史归档/01_客户原始资料与口径/客户GLM原始部署资料_20261023/README.md`）；P节点原脚本（`../../../06_客户资料与历史归档/01_客户原始资料与口径/客户GLM原始部署资料_20261023/start_p1.sh`）；D原脚本（`../../../06_客户资料与历史归档/01_客户原始资料与口径/客户GLM原始部署资料_20261023/start_d.sh`）。

## 五、实际验证配置与原生命令

两组分别写全，不以“与上一组相同”省略P、四D、代理或压测命令。先在对应终端执行环境与工作目录命令，然后使用其下以 `vllm serve` 开头的模型命令；LMCache使用 `lmcache server`。不含资源巡检、进程清退、后台运行及轮询包装。

以下路径、IP、端口与参数按当时归档保留，适用于已准备好的对应容器。模型和客户数据已在本地，不包含重新下载模型或安装环境的步骤。切换OFF／ON时实际保留D与代理，不需要因为两组均列出完整命令而重复启动它们。

P与LMCache的环境固定使用 `/opt/ks-night-luv-20261023/runtime/baseline` 和原有私有venv；D使用自己的已安装环境。这里不以目录中的版本字符串替代完整包版本证明。参数末尾的profiler配置属于已有观测入口，不等于正式轮开启了设备profiler采集。

### 5.1 LMCache OFF：完整环境、P、四D、代理和benchmark命令

**本组复现位置**：8服务器容器 `ks-pp-luv-m8-priv` 运行P；12服务器容器 `ks-pp-m12-priv` 运行四个D、代理和benchmark。下列长驻服务分别在独立终端运行，不添加后台、轮询或清退包装。

**顺序**：四D与代理就绪，启动P；完成最长请求诊断，再执行正式benchmark。

本组P仅使用Mooncake Connector，不连接LMCache。实测期间独立缓存进程被保留，但不参与OFF请求；单独复现本组无需启动LMCache。

#### 8／P容器：P模型

**运行前环境与工作目录（先在同一终端执行）**

```bash
export KS_MP=/opt/ks-lmcache-stage2/official-mp-v0.5.6rc5
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/pp2/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/stages/off/tmp/p
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/pp2/xdg
export HF_HOME="$XDG_CACHE_HOME/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HUGGINGFACE_HUB_CACHE="$HF_HUB_CACHE"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
export TORCH_HOME="$XDG_CACHE_HOME/torch"
export VLLM_CACHE_ROOT="$XDG_CACHE_HOME/vllm"
unset TRANSFORMERS_CACHE
export PATH="$KS_MP/venv/bin:$PATH"
export PYTHONPATH="/opt/ks-night-luv-20261023/runtime/baseline:$KS_MP/LMCache-Ascend-official-v0.5.6rc5:/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe"
export PYTHONDONTWRITEBYTECODE=1
export LMCACHE_TRACK_USAGE=false
unset TORCH_DEVICE_BACKEND_AUTOLOAD LMCACHE_CONFIG_FILE
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
export PYTHONHASHSEED=0 TE_PARALLEL_COMPILER=1
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}:/usr/local/lib"
export VLLM_ASCEND_ENABLE_FUSED_MC2=1 HCCL_OP_EXPANSION_MODE=AIV
export HCCL_IF_IP=192.168.0.110 HCCL_NPU_SOCKET_PORT_RANGE=auto
export GLOO_SOCKET_IFNAME=enp23s0f3 TP_SOCKET_IFNAME=enp23s0f3 HCCL_SOCKET_IFNAME=enp23s0f3
export OMP_PROC_BIND=false OMP_NUM_THREADS=1
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:False HCCL_BUFFSIZE=400
export ACL_OP_INIT_MODE=1 ASCEND_A3_ENABLE=1 VLLM_ASCEND_ENABLE_FLASHCOMM1=1
export VLLM_PP_LAYER_PARTITION=41,37
export KS_TTFT_TRACE_DIR=/opt/ks-metrics-supplement-20261025/stages/off/traces
mkdir -p /opt/ks-metrics-supplement-20261025/stages/off/work/p "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$KS_TTFT_TRACE_DIR" /opt/ks-metrics-supplement-20261025/stages/off/profiles
cd /opt/ks-metrics-supplement-20261025/stages/off/work/p
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18100 \
  --tensor-parallel-size 8 \
  --pipeline-parallel-size 2 \
  --enable-expert-parallel \
  --distributed-executor-backend mp \
  --master-addr 127.0.0.1 \
  --master-port 18190 \
  --nnodes 1 \
  --node-rank 0 \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 49152 \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":false,"multistream_overlap_shared_expert":true,"enable_dsa_cp":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true,"c8_enable_reshape_optim":true}' \
  --max-num-batched-tokens 16384 \
  --max-num-seqs 64 \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --quantization ascend \
  --gpu-memory-utilization 0.94 \
  --enforce-eager \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_producer","kv_port":"28000","engine_id":"ks-p-p1d1-pp2","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}}' \
  --profiler-config '{"profiler":"torch","torch_profiler_dir":"/opt/ks-metrics-supplement-20261025/stages/off/profiles","ignore_frontend":true,"torch_profiler_with_stack":false,"torch_profiler_with_memory":false,"torch_profiler_record_shapes":false,"torch_profiler_with_flops":false,"delay_iterations":0,"max_iterations":0}'
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/p/off.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/p/off.serve.native.sh`）。

#### 12／D容器：D rank0（端口18200，NPU 0—3）

**运行前环境与工作目录（先在同一终端执行）**

```bash
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3
export ACL_OP_INIT_MODE=1
export ASCEND_TOOLKIT_LATEST_HOME=/usr/local/Ascend/ascend-toolkit/latest
export HCCL_SOCKET_IFNAME=enp23s0f3
export ATB_OPSRUNNER_KERNEL_CACHE_LOCAL_COUNT=1
export ATB_STREAM_SYNC_EVERY_RUNNER_ENABLE=0
export PYTHONHASHSEED=0
export KS_NODE=M12
export ATB_STREAM_SYNC_EVERY_KERNEL_ENABLE=0
export ATB_CXX_ABI=1
export ATB_OPSRUNNER_KERNEL_CACHE_GLOABL_COUNT=5
export GLOO_SOCKET_IFNAME=enp23s0f3
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export KS_CONTAINER=ks-pp-m12-priv
export HCCL_BUFFSIZE=256
export ATB_HOME_PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1
export VLLM_ASCEND_ENABLE_FUSED_MC2=1
export CMAKE_PREFIX_PATH=/usr/local/Ascend/cann-9.1.0/toolkit/tools/tikicpulib/lib/cmake:/usr/local/Ascend/cann-9.1.0/lib64/cmake
export TOOLCHAIN_HOME=/usr/local/Ascend/cann-9.1.0/toolkit
export HCCL_OP_EXPANSION_MODE=AIV
export OMP_PROC_BIND=false
export ASCEND_TOOLKIT_HOME=/usr/local/Ascend/cann-9.1.0
export KS_RUN=/opt/ks-metrics-supplement-20261025/work/d0
export PYTHONPATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe:
export LCCL_DETERMINISTIC=0
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_MLAPO=1
export TP_SOCKET_IFNAME=enp23s0f3
export ATB_COMPARE_TILING_EVERY_KERNEL=0
export ASCEND_OPP_PATH=/usr/local/Ascend/cann-9.1.0/opp
export LD_LIBRARY_PATH=/usr/local/Ascend/cann-9.1.0/lib64:/usr/local/Ascend/cann-9.1.0/lib64/plugin/opskernel:/usr/local/Ascend/cann-9.1.0/lib64/plugin/nnengine:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/lib:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/examples:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/tests/atbopstest:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64/plugin:/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/python3.12.13/lib::/usr/local/lib
export LCCL_PARALLEL=0
export SOC_VERSION=ascend910_9391
export ASCEND_AICPU_PATH=/usr/local/Ascend/cann-9.1.0
export OMP_NUM_THREADS=1
export ATB_STREAM_SYNC_EVERY_OPERATION_ENABLE=0
export PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/bin:/usr/local/Ascend/ascend-toolkit/latest/bin:/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/bin:/usr/local/Ascend/ascend-toolkit/latest/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/show_kernel_debug_data:/usr/local/Ascend/cann-9.1.0/tools/msobjdump:/usr/local/Ascend/cann-9.1.0/bin:/usr/local/Ascend/cann-9.1.0/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/profiler/bin:/usr/local/Ascend/cann-9.1.0/tools/ascend_system_advisor/asys:/usr/local/python3.12.13/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ASCEND_HOME_PATH=/usr/local/Ascend/cann-9.1.0
export HCCL_IF_IP=192.168.0.50
export ATB_MATMUL_SHUFFLE_K_ENABLE=1
export ASCEND_A3_ENABLE=1
export DEBIAN_FRONTEND=noninteractive
export ATB_WORKSPACE_MEM_ALLOC_ALG_TYPE=1
export ATB_SHARE_MEMORY_NAME_SUFFIX=''
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/d0/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/work/d0/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/d0/xdg
export HF_HOME=/opt/ks-metrics-supplement-20261025/cache/d0/xdg/huggingface
export PYTHONDONTWRITEBYTECODE=1
mkdir -p /opt/ks-metrics-supplement-20261025/work/d0 "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$HF_HOME"
cd /opt/ks-metrics-supplement-20261025/work/d0
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18200 \
  --data-parallel-size 4 \
  --data-parallel-rank 0 \
  --data-parallel-address 192.168.0.50 \
  --data-parallel-rpc-port 18300 \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --enable-expert-parallel \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 65536 \
  --max-num-batched-tokens 164 \
  --max-num-seqs 32 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":true,"multistream_overlap_shared_expert":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true}' \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --gpu-memory-utilization 0.92 \
  --async-scheduling \
  --quantization ascend \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_consumer","kv_port":"29000","engine_id":"ks-d-off-0","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}}' \
  --kv-cache-memory-bytes 4831838208
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/d/d0.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/d/d0.serve.native.sh`）。

#### 12／D容器：D rank1（端口18201，NPU 4—7）

**运行前环境与工作目录（先在同一终端执行）**

```bash
export ASCEND_RT_VISIBLE_DEVICES=4,5,6,7
export ACL_OP_INIT_MODE=1
export ASCEND_TOOLKIT_LATEST_HOME=/usr/local/Ascend/ascend-toolkit/latest
export HCCL_SOCKET_IFNAME=enp23s0f3
export ATB_OPSRUNNER_KERNEL_CACHE_LOCAL_COUNT=1
export ATB_STREAM_SYNC_EVERY_RUNNER_ENABLE=0
export PYTHONHASHSEED=0
export KS_NODE=M12
export ATB_STREAM_SYNC_EVERY_KERNEL_ENABLE=0
export ATB_CXX_ABI=1
export ATB_OPSRUNNER_KERNEL_CACHE_GLOABL_COUNT=5
export GLOO_SOCKET_IFNAME=enp23s0f3
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export KS_CONTAINER=ks-pp-m12-priv
export HCCL_BUFFSIZE=256
export ATB_HOME_PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1
export VLLM_ASCEND_ENABLE_FUSED_MC2=1
export CMAKE_PREFIX_PATH=/usr/local/Ascend/cann-9.1.0/toolkit/tools/tikicpulib/lib/cmake:/usr/local/Ascend/cann-9.1.0/lib64/cmake
export TOOLCHAIN_HOME=/usr/local/Ascend/cann-9.1.0/toolkit
export HCCL_OP_EXPANSION_MODE=AIV
export OMP_PROC_BIND=false
export ASCEND_TOOLKIT_HOME=/usr/local/Ascend/cann-9.1.0
export KS_RUN=/opt/ks-metrics-supplement-20261025/work/d1
export PYTHONPATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe:
export LCCL_DETERMINISTIC=0
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_MLAPO=1
export TP_SOCKET_IFNAME=enp23s0f3
export ATB_COMPARE_TILING_EVERY_KERNEL=0
export ASCEND_OPP_PATH=/usr/local/Ascend/cann-9.1.0/opp
export LD_LIBRARY_PATH=/usr/local/Ascend/cann-9.1.0/lib64:/usr/local/Ascend/cann-9.1.0/lib64/plugin/opskernel:/usr/local/Ascend/cann-9.1.0/lib64/plugin/nnengine:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/lib:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/examples:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/tests/atbopstest:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64/plugin:/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/python3.12.13/lib::/usr/local/lib
export LCCL_PARALLEL=0
export SOC_VERSION=ascend910_9391
export ASCEND_AICPU_PATH=/usr/local/Ascend/cann-9.1.0
export OMP_NUM_THREADS=1
export ATB_STREAM_SYNC_EVERY_OPERATION_ENABLE=0
export PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/bin:/usr/local/Ascend/ascend-toolkit/latest/bin:/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/bin:/usr/local/Ascend/ascend-toolkit/latest/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/show_kernel_debug_data:/usr/local/Ascend/cann-9.1.0/tools/msobjdump:/usr/local/Ascend/cann-9.1.0/bin:/usr/local/Ascend/cann-9.1.0/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/profiler/bin:/usr/local/Ascend/cann-9.1.0/tools/ascend_system_advisor/asys:/usr/local/python3.12.13/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ASCEND_HOME_PATH=/usr/local/Ascend/cann-9.1.0
export HCCL_IF_IP=192.168.0.50
export ATB_MATMUL_SHUFFLE_K_ENABLE=1
export ASCEND_A3_ENABLE=1
export DEBIAN_FRONTEND=noninteractive
export ATB_WORKSPACE_MEM_ALLOC_ALG_TYPE=1
export ATB_SHARE_MEMORY_NAME_SUFFIX=''
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/d1/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/work/d1/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/d1/xdg
export HF_HOME=/opt/ks-metrics-supplement-20261025/cache/d1/xdg/huggingface
export PYTHONDONTWRITEBYTECODE=1
mkdir -p /opt/ks-metrics-supplement-20261025/work/d1 "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$HF_HOME"
cd /opt/ks-metrics-supplement-20261025/work/d1
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18201 \
  --data-parallel-size 4 \
  --data-parallel-rank 1 \
  --data-parallel-address 192.168.0.50 \
  --data-parallel-rpc-port 18300 \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --enable-expert-parallel \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 65536 \
  --max-num-batched-tokens 164 \
  --max-num-seqs 32 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":true,"multistream_overlap_shared_expert":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true}' \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --gpu-memory-utilization 0.92 \
  --async-scheduling \
  --quantization ascend \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_consumer","kv_port":"29000","engine_id":"ks-d-off-1","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}}' \
  --kv-cache-memory-bytes 4831838208
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/d/d1.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/d/d1.serve.native.sh`）。

#### 12／D容器：D rank2（端口18202，NPU 8—11）

**运行前环境与工作目录（先在同一终端执行）**

```bash
export ASCEND_RT_VISIBLE_DEVICES=8,9,10,11
export ACL_OP_INIT_MODE=1
export ASCEND_TOOLKIT_LATEST_HOME=/usr/local/Ascend/ascend-toolkit/latest
export HCCL_SOCKET_IFNAME=enp23s0f3
export ATB_OPSRUNNER_KERNEL_CACHE_LOCAL_COUNT=1
export ATB_STREAM_SYNC_EVERY_RUNNER_ENABLE=0
export PYTHONHASHSEED=0
export KS_NODE=M12
export ATB_STREAM_SYNC_EVERY_KERNEL_ENABLE=0
export ATB_CXX_ABI=1
export ATB_OPSRUNNER_KERNEL_CACHE_GLOABL_COUNT=5
export GLOO_SOCKET_IFNAME=enp23s0f3
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export KS_CONTAINER=ks-pp-m12-priv
export HCCL_BUFFSIZE=256
export ATB_HOME_PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1
export VLLM_ASCEND_ENABLE_FUSED_MC2=1
export CMAKE_PREFIX_PATH=/usr/local/Ascend/cann-9.1.0/toolkit/tools/tikicpulib/lib/cmake:/usr/local/Ascend/cann-9.1.0/lib64/cmake
export TOOLCHAIN_HOME=/usr/local/Ascend/cann-9.1.0/toolkit
export HCCL_OP_EXPANSION_MODE=AIV
export OMP_PROC_BIND=false
export ASCEND_TOOLKIT_HOME=/usr/local/Ascend/cann-9.1.0
export KS_RUN=/opt/ks-metrics-supplement-20261025/work/d2
export PYTHONPATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe:
export LCCL_DETERMINISTIC=0
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_MLAPO=1
export TP_SOCKET_IFNAME=enp23s0f3
export ATB_COMPARE_TILING_EVERY_KERNEL=0
export ASCEND_OPP_PATH=/usr/local/Ascend/cann-9.1.0/opp
export LD_LIBRARY_PATH=/usr/local/Ascend/cann-9.1.0/lib64:/usr/local/Ascend/cann-9.1.0/lib64/plugin/opskernel:/usr/local/Ascend/cann-9.1.0/lib64/plugin/nnengine:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/lib:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/examples:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/tests/atbopstest:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64/plugin:/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/python3.12.13/lib::/usr/local/lib
export LCCL_PARALLEL=0
export SOC_VERSION=ascend910_9391
export ASCEND_AICPU_PATH=/usr/local/Ascend/cann-9.1.0
export OMP_NUM_THREADS=1
export ATB_STREAM_SYNC_EVERY_OPERATION_ENABLE=0
export PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/bin:/usr/local/Ascend/ascend-toolkit/latest/bin:/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/bin:/usr/local/Ascend/ascend-toolkit/latest/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/show_kernel_debug_data:/usr/local/Ascend/cann-9.1.0/tools/msobjdump:/usr/local/Ascend/cann-9.1.0/bin:/usr/local/Ascend/cann-9.1.0/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/profiler/bin:/usr/local/Ascend/cann-9.1.0/tools/ascend_system_advisor/asys:/usr/local/python3.12.13/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ASCEND_HOME_PATH=/usr/local/Ascend/cann-9.1.0
export HCCL_IF_IP=192.168.0.50
export ATB_MATMUL_SHUFFLE_K_ENABLE=1
export ASCEND_A3_ENABLE=1
export DEBIAN_FRONTEND=noninteractive
export ATB_WORKSPACE_MEM_ALLOC_ALG_TYPE=1
export ATB_SHARE_MEMORY_NAME_SUFFIX=''
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/d2/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/work/d2/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/d2/xdg
export HF_HOME=/opt/ks-metrics-supplement-20261025/cache/d2/xdg/huggingface
export PYTHONDONTWRITEBYTECODE=1
mkdir -p /opt/ks-metrics-supplement-20261025/work/d2 "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$HF_HOME"
cd /opt/ks-metrics-supplement-20261025/work/d2
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18202 \
  --data-parallel-size 4 \
  --data-parallel-rank 2 \
  --data-parallel-address 192.168.0.50 \
  --data-parallel-rpc-port 18300 \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --enable-expert-parallel \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 65536 \
  --max-num-batched-tokens 164 \
  --max-num-seqs 32 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":true,"multistream_overlap_shared_expert":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true}' \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --gpu-memory-utilization 0.92 \
  --async-scheduling \
  --quantization ascend \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_consumer","kv_port":"29000","engine_id":"ks-d-off-2","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}}' \
  --kv-cache-memory-bytes 4831838208
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/d/d2.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/d/d2.serve.native.sh`）。

#### 12／D容器：D rank3（端口18203，NPU 12—15）

**运行前环境与工作目录（先在同一终端执行）**

```bash
export ASCEND_RT_VISIBLE_DEVICES=12,13,14,15
export ACL_OP_INIT_MODE=1
export ASCEND_TOOLKIT_LATEST_HOME=/usr/local/Ascend/ascend-toolkit/latest
export HCCL_SOCKET_IFNAME=enp23s0f3
export ATB_OPSRUNNER_KERNEL_CACHE_LOCAL_COUNT=1
export ATB_STREAM_SYNC_EVERY_RUNNER_ENABLE=0
export PYTHONHASHSEED=0
export KS_NODE=M12
export ATB_STREAM_SYNC_EVERY_KERNEL_ENABLE=0
export ATB_CXX_ABI=1
export ATB_OPSRUNNER_KERNEL_CACHE_GLOABL_COUNT=5
export GLOO_SOCKET_IFNAME=enp23s0f3
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export KS_CONTAINER=ks-pp-m12-priv
export HCCL_BUFFSIZE=256
export ATB_HOME_PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1
export VLLM_ASCEND_ENABLE_FUSED_MC2=1
export CMAKE_PREFIX_PATH=/usr/local/Ascend/cann-9.1.0/toolkit/tools/tikicpulib/lib/cmake:/usr/local/Ascend/cann-9.1.0/lib64/cmake
export TOOLCHAIN_HOME=/usr/local/Ascend/cann-9.1.0/toolkit
export HCCL_OP_EXPANSION_MODE=AIV
export OMP_PROC_BIND=false
export ASCEND_TOOLKIT_HOME=/usr/local/Ascend/cann-9.1.0
export KS_RUN=/opt/ks-metrics-supplement-20261025/work/d3
export PYTHONPATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe:
export LCCL_DETERMINISTIC=0
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_MLAPO=1
export TP_SOCKET_IFNAME=enp23s0f3
export ATB_COMPARE_TILING_EVERY_KERNEL=0
export ASCEND_OPP_PATH=/usr/local/Ascend/cann-9.1.0/opp
export LD_LIBRARY_PATH=/usr/local/Ascend/cann-9.1.0/lib64:/usr/local/Ascend/cann-9.1.0/lib64/plugin/opskernel:/usr/local/Ascend/cann-9.1.0/lib64/plugin/nnengine:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/lib:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/examples:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/tests/atbopstest:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64/plugin:/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/python3.12.13/lib::/usr/local/lib
export LCCL_PARALLEL=0
export SOC_VERSION=ascend910_9391
export ASCEND_AICPU_PATH=/usr/local/Ascend/cann-9.1.0
export OMP_NUM_THREADS=1
export ATB_STREAM_SYNC_EVERY_OPERATION_ENABLE=0
export PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/bin:/usr/local/Ascend/ascend-toolkit/latest/bin:/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/bin:/usr/local/Ascend/ascend-toolkit/latest/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/show_kernel_debug_data:/usr/local/Ascend/cann-9.1.0/tools/msobjdump:/usr/local/Ascend/cann-9.1.0/bin:/usr/local/Ascend/cann-9.1.0/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/profiler/bin:/usr/local/Ascend/cann-9.1.0/tools/ascend_system_advisor/asys:/usr/local/python3.12.13/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ASCEND_HOME_PATH=/usr/local/Ascend/cann-9.1.0
export HCCL_IF_IP=192.168.0.50
export ATB_MATMUL_SHUFFLE_K_ENABLE=1
export ASCEND_A3_ENABLE=1
export DEBIAN_FRONTEND=noninteractive
export ATB_WORKSPACE_MEM_ALLOC_ALG_TYPE=1
export ATB_SHARE_MEMORY_NAME_SUFFIX=''
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/d3/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/work/d3/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/d3/xdg
export HF_HOME=/opt/ks-metrics-supplement-20261025/cache/d3/xdg/huggingface
export PYTHONDONTWRITEBYTECODE=1
mkdir -p /opt/ks-metrics-supplement-20261025/work/d3 "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$HF_HOME"
cd /opt/ks-metrics-supplement-20261025/work/d3
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18203 \
  --data-parallel-size 4 \
  --data-parallel-rank 3 \
  --data-parallel-address 192.168.0.50 \
  --data-parallel-rpc-port 18300 \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --enable-expert-parallel \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 65536 \
  --max-num-batched-tokens 164 \
  --max-num-seqs 32 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":true,"multistream_overlap_shared_expert":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true}' \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --gpu-memory-utilization 0.92 \
  --async-scheduling \
  --quantization ascend \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_consumer","kv_port":"29000","engine_id":"ks-d-off-3","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}}' \
  --kv-cache-memory-bytes 4831838208
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/d/d3.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/d/d3.serve.native.sh`）。

#### 12／D容器：PD代理

这是现有代理的原生入口；初始化逻辑位于其主入口，保留Python服务命令。

```bash
set -e
export HOME=/opt/ks-m8p-m12d-luv-20261023/runtime/proxy/home
export TMPDIR=/opt/ks-m8p-m12d-luv-20261023/runtime/proxy/tmp
export XDG_CACHE_HOME=/opt/ks-m8p-m12d-luv-20261023/runtime/proxy/cache
export HF_HOME="$XDG_CACHE_HOME/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HUGGINGFACE_HUB_CACHE="$HF_HUB_CACHE"
export VLLM_CACHE_ROOT="$XDG_CACHE_HOME/vllm"
export PYTHONDONTWRITEBYTECODE=1
export NO_PROXY=127.0.0.1,localhost,192.168.0.110,192.168.0.50
export no_proxy="$NO_PROXY"
unset PYTHONPATH TRANSFORMERS_CACHE
mkdir -p "$HOME" "$TMPDIR" "$XDG_CACHE_HOME"
cd /opt/ks-m8p-m12d-luv-20261023/bench
/usr/local/python3.12.13/bin/python3 proxy_v023.py \
  --host 127.0.0.1 --port 18080 --workers 1 --max-retries 1 \
  --prefiller-hosts 192.168.0.110 --prefiller-ports 18100 \
  --decoder-hosts 192.168.0.50 192.168.0.50 192.168.0.50 192.168.0.50 \
  --decoder-ports 18200 18201 18202 18203
```

来源：代理命令原件（`../../01_环境与原生启动配置/8作P与12作D静态准备_20261023/bench/proxy-m12.native.sh`）。

#### 12／D容器：客户原文最长请求容量诊断

正式轮之前单独执行1条最长客户请求，验证容量；该条不计入389条正式结果。

**benchmark运行环境（诊断与正式轮均先在该终端执行）**

```bash
unset PYTHONPATH KS_BENCH_INPUT_LOG
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR=/opt/ks-metrics-supplement-20261025/benchmark/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/benchmark/cache
export HF_HOME="$XDG_CACHE_HOME/huggingface"
export VLLM_CACHE_ROOT="$XDG_CACHE_HOME/vllm"
export NO_PROXY=127.0.0.1,localhost,192.168.0.110,192.168.0.50
export no_proxy="$NO_PROXY"
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME" /opt/ks-metrics-supplement-20261025/stages/off/results
cd /opt/ks-metrics-supplement-20261025/benchmark
```

```bash
vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:18080 \
  --endpoint /v1/completions \
  --model glm-5 \
  --tokenizer /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --dataset-name custom \
  --dataset-path /opt/ks-customer-bench-20261024/benchmark/capacity-longest-49152.jsonl \
  --custom-output-len -1 \
  --disable-shuffle \
  --skip-chat-template \
  --no-oversample \
  --num-prompts 1 \
  --max-concurrency 1 \
  --request-rate inf \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --temperature 0 \
  --ignore-eos \
  --seed 0 \
  --percentile-metrics ttft,tpot,itl,e2el \
  --metric-percentiles 50,90,95,99 \
  --save-result \
  --save-detailed \
  --result-dir /opt/ks-metrics-supplement-20261025/stages/off/diagnostic \
  --result-filename benchmark.json
```

#### 12／D容器：正式benchmark（389条客户原文）

```bash
vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:18080 \
  --endpoint /v1/completions \
  --model glm-5 \
  --tokenizer /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --dataset-name custom \
  --dataset-path /opt/ks-customer-bench-20261024/capacity-pools-20261024T110510324465Z/cap_49152/custom.jsonl \
  --custom-output-len -1 \
  --disable-shuffle \
  --skip-chat-template \
  --no-oversample \
  --num-prompts 389 \
  --max-concurrency 4 \
  --request-rate inf \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --temperature 0 \
  --ignore-eos \
  --seed 0 \
  --percentile-metrics ttft,tpot,itl,e2el \
  --metric-percentiles 50,90,95,99 \
  --save-result \
  --save-detailed \
  --result-dir /opt/ks-metrics-supplement-20261025/stages/off/results \
  --result-filename benchmark.json
```

上面的 `vllm bench serve` 是与实际请求参数一致的原生命令。10/25采集时用已有 `client_observe.py bench serve` 调用同一benchmark，额外记录逐请求时间；直接运行原生命令不会产生同样的 `client.jsonl`。原采集参数保留不改，本报告不展示流块指标。

完整原生与观测入口记录：实际benchmark启动回执（`../../20261025_性能指标补采/KV动作聚合/off/正式元数据/stages/off/results/launch.json`）。两组请求参数相同，只有结果目录按组区分；结果路径是当时归档路径，重跑时须另选结果目录，避免覆盖旧结果。

### 5.2 LMCache ON（原异步）：完整环境、P、四D、代理和benchmark命令

**本组复现位置**：8服务器容器 `ks-pp-luv-m8-priv` 运行P和LMCache；12服务器容器 `ks-pp-m12-priv` 运行四个D、代理和benchmark。下列长驻服务分别在独立终端运行，不添加后台、轮询或清退包装。

**顺序**：四D与代理就绪，启动LMCache，再启动P；完成最长请求诊断后清空并验空缓存，再执行正式benchmark。

#### 8／P容器：LMCache缓存服务

**运行前环境与工作目录（先在同一终端执行）**

```bash
export KS_TTFT_TRACE_DIR=/opt/ks-metrics-supplement-20261025/stages/cache/traces
export KS_MP=/opt/ks-lmcache-stage2/official-mp-v0.5.6rc5
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/pp2/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/stages/cache/tmp/cache
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/pp2/xdg
export HF_HOME="$XDG_CACHE_HOME/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HUGGINGFACE_HUB_CACHE="$HF_HUB_CACHE"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
export TORCH_HOME="$XDG_CACHE_HOME/torch"
export VLLM_CACHE_ROOT="$XDG_CACHE_HOME/vllm"
unset TRANSFORMERS_CACHE
export PATH="$KS_MP/venv/bin:$PATH"
export PYTHONPATH="/opt/ks-night-luv-20261023/runtime/baseline:$KS_MP/LMCache-Ascend-official-v0.5.6rc5:/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe"
export PYTHONDONTWRITEBYTECODE=1
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
export LMCACHE_TRACK_USAGE=false
unset TORCH_DEVICE_BACKEND_AUTOLOAD LMCACHE_CONFIG_FILE
mkdir -p /opt/ks-metrics-supplement-20261025/stages/cache/work/cache "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$KS_TTFT_TRACE_DIR"
cd /opt/ks-metrics-supplement-20261025/stages/cache/work/cache
```

```bash
lmcache server --host 127.0.0.1 --port 5565 --http-host 127.0.0.1 --http-port 15565 --chunk-size 512 --supported-transfer-mode lmcache_driven --l1-size-gb 12 --eviction-policy LRU --l1-align-bytes 65536 --disable-metrics --instance-id ks-luv-customer-20261024 --max-gpu-workers 1
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/cache/server.shared.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/cache/server.shared.native.sh`）。

缓存使用12GiB CPU内存、chunk512、LRU和1个GPU任务处理线程。`--max-gpu-workers 1`是缓存任务处理并发参数，不是模型TP数。本报告没有采用后续双线程诊断的配置。

#### 8／P容器：P模型

**运行前环境与工作目录（先在同一终端执行）**

```bash
export KS_MP=/opt/ks-lmcache-stage2/official-mp-v0.5.6rc5
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/pp2/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/stages/on-async/tmp/p
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/pp2/xdg
export HF_HOME="$XDG_CACHE_HOME/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HUGGINGFACE_HUB_CACHE="$HF_HUB_CACHE"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
export TORCH_HOME="$XDG_CACHE_HOME/torch"
export VLLM_CACHE_ROOT="$XDG_CACHE_HOME/vllm"
unset TRANSFORMERS_CACHE
export PATH="$KS_MP/venv/bin:$PATH"
export PYTHONPATH="/opt/ks-night-luv-20261023/runtime/baseline:$KS_MP/LMCache-Ascend-official-v0.5.6rc5:/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe"
export PYTHONDONTWRITEBYTECODE=1
export LMCACHE_TRACK_USAGE=false
unset TORCH_DEVICE_BACKEND_AUTOLOAD LMCACHE_CONFIG_FILE
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15
export PYTHONHASHSEED=0 TE_PARALLEL_COMPILER=1
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}:/usr/local/lib"
export VLLM_ASCEND_ENABLE_FUSED_MC2=1 HCCL_OP_EXPANSION_MODE=AIV
export HCCL_IF_IP=192.168.0.110 HCCL_NPU_SOCKET_PORT_RANGE=auto
export GLOO_SOCKET_IFNAME=enp23s0f3 TP_SOCKET_IFNAME=enp23s0f3 HCCL_SOCKET_IFNAME=enp23s0f3
export OMP_PROC_BIND=false OMP_NUM_THREADS=1
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:False HCCL_BUFFSIZE=400
export ACL_OP_INIT_MODE=1 ASCEND_A3_ENABLE=1 VLLM_ASCEND_ENABLE_FLASHCOMM1=1
export VLLM_PP_LAYER_PARTITION=41,37
export KS_TTFT_TRACE_DIR=/opt/ks-metrics-supplement-20261025/stages/on-async/traces
mkdir -p /opt/ks-metrics-supplement-20261025/stages/on-async/work/p "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$KS_TTFT_TRACE_DIR" /opt/ks-metrics-supplement-20261025/stages/on-async/profiles
cd /opt/ks-metrics-supplement-20261025/stages/on-async/work/p
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18100 \
  --tensor-parallel-size 8 \
  --pipeline-parallel-size 2 \
  --enable-expert-parallel \
  --distributed-executor-backend mp \
  --master-addr 127.0.0.1 \
  --master-port 18190 \
  --nnodes 1 \
  --node-rank 0 \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 49152 \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":false,"multistream_overlap_shared_expert":true,"enable_dsa_cp":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true,"c8_enable_reshape_optim":true}' \
  --max-num-batched-tokens 16384 \
  --max-num-seqs 64 \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --quantization ascend \
  --gpu-memory-utilization 0.94 \
  --enforce-eager \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_role":"kv_producer","kv_port":"28000","engine_id":"ks-p-p1d1-pp2","kv_connector":"MultiConnector","kv_connector_extra_config":{"connectors":[{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_producer","kv_port":"28000","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}},{"kv_connector":"LMCacheMPConnector","kv_role":"kv_producer","kv_connector_module_path":"lmcache.integration.vllm.lmcache_mp_connector","kv_connector_extra_config":{"lmcache.mp.host":"tcp://127.0.0.1","lmcache.mp.port":5565,"lmcache.mp.autostart":false,"lmcache.mp.nonblocking_lookup_status":false}}]}}' \
  --profiler-config '{"profiler":"torch","torch_profiler_dir":"/opt/ks-metrics-supplement-20261025/stages/on-async/profiles","ignore_frontend":true,"torch_profiler_with_stack":false,"torch_profiler_with_memory":false,"torch_profiler_record_shapes":false,"torch_profiler_with_flops":false,"delay_iterations":0,"max_iterations":0}'
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/p/on-async.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/p/on-async.serve.native.sh`）。

#### 12／D容器：D rank0（端口18200，NPU 0—3）

**运行前环境与工作目录（先在同一终端执行）**

```bash
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3
export ACL_OP_INIT_MODE=1
export ASCEND_TOOLKIT_LATEST_HOME=/usr/local/Ascend/ascend-toolkit/latest
export HCCL_SOCKET_IFNAME=enp23s0f3
export ATB_OPSRUNNER_KERNEL_CACHE_LOCAL_COUNT=1
export ATB_STREAM_SYNC_EVERY_RUNNER_ENABLE=0
export PYTHONHASHSEED=0
export KS_NODE=M12
export ATB_STREAM_SYNC_EVERY_KERNEL_ENABLE=0
export ATB_CXX_ABI=1
export ATB_OPSRUNNER_KERNEL_CACHE_GLOABL_COUNT=5
export GLOO_SOCKET_IFNAME=enp23s0f3
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export KS_CONTAINER=ks-pp-m12-priv
export HCCL_BUFFSIZE=256
export ATB_HOME_PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1
export VLLM_ASCEND_ENABLE_FUSED_MC2=1
export CMAKE_PREFIX_PATH=/usr/local/Ascend/cann-9.1.0/toolkit/tools/tikicpulib/lib/cmake:/usr/local/Ascend/cann-9.1.0/lib64/cmake
export TOOLCHAIN_HOME=/usr/local/Ascend/cann-9.1.0/toolkit
export HCCL_OP_EXPANSION_MODE=AIV
export OMP_PROC_BIND=false
export ASCEND_TOOLKIT_HOME=/usr/local/Ascend/cann-9.1.0
export KS_RUN=/opt/ks-metrics-supplement-20261025/work/d0
export PYTHONPATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe:
export LCCL_DETERMINISTIC=0
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_MLAPO=1
export TP_SOCKET_IFNAME=enp23s0f3
export ATB_COMPARE_TILING_EVERY_KERNEL=0
export ASCEND_OPP_PATH=/usr/local/Ascend/cann-9.1.0/opp
export LD_LIBRARY_PATH=/usr/local/Ascend/cann-9.1.0/lib64:/usr/local/Ascend/cann-9.1.0/lib64/plugin/opskernel:/usr/local/Ascend/cann-9.1.0/lib64/plugin/nnengine:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/lib:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/examples:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/tests/atbopstest:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64/plugin:/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/python3.12.13/lib::/usr/local/lib
export LCCL_PARALLEL=0
export SOC_VERSION=ascend910_9391
export ASCEND_AICPU_PATH=/usr/local/Ascend/cann-9.1.0
export OMP_NUM_THREADS=1
export ATB_STREAM_SYNC_EVERY_OPERATION_ENABLE=0
export PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/bin:/usr/local/Ascend/ascend-toolkit/latest/bin:/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/bin:/usr/local/Ascend/ascend-toolkit/latest/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/show_kernel_debug_data:/usr/local/Ascend/cann-9.1.0/tools/msobjdump:/usr/local/Ascend/cann-9.1.0/bin:/usr/local/Ascend/cann-9.1.0/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/profiler/bin:/usr/local/Ascend/cann-9.1.0/tools/ascend_system_advisor/asys:/usr/local/python3.12.13/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ASCEND_HOME_PATH=/usr/local/Ascend/cann-9.1.0
export HCCL_IF_IP=192.168.0.50
export ATB_MATMUL_SHUFFLE_K_ENABLE=1
export ASCEND_A3_ENABLE=1
export DEBIAN_FRONTEND=noninteractive
export ATB_WORKSPACE_MEM_ALLOC_ALG_TYPE=1
export ATB_SHARE_MEMORY_NAME_SUFFIX=''
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/d0/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/work/d0/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/d0/xdg
export HF_HOME=/opt/ks-metrics-supplement-20261025/cache/d0/xdg/huggingface
export PYTHONDONTWRITEBYTECODE=1
mkdir -p /opt/ks-metrics-supplement-20261025/work/d0 "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$HF_HOME"
cd /opt/ks-metrics-supplement-20261025/work/d0
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18200 \
  --data-parallel-size 4 \
  --data-parallel-rank 0 \
  --data-parallel-address 192.168.0.50 \
  --data-parallel-rpc-port 18300 \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --enable-expert-parallel \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 65536 \
  --max-num-batched-tokens 164 \
  --max-num-seqs 32 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":true,"multistream_overlap_shared_expert":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true}' \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --gpu-memory-utilization 0.92 \
  --async-scheduling \
  --quantization ascend \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_consumer","kv_port":"29000","engine_id":"ks-d-off-0","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}}' \
  --kv-cache-memory-bytes 4831838208
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/d/d0.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/d/d0.serve.native.sh`）。

#### 12／D容器：D rank1（端口18201，NPU 4—7）

**运行前环境与工作目录（先在同一终端执行）**

```bash
export ASCEND_RT_VISIBLE_DEVICES=4,5,6,7
export ACL_OP_INIT_MODE=1
export ASCEND_TOOLKIT_LATEST_HOME=/usr/local/Ascend/ascend-toolkit/latest
export HCCL_SOCKET_IFNAME=enp23s0f3
export ATB_OPSRUNNER_KERNEL_CACHE_LOCAL_COUNT=1
export ATB_STREAM_SYNC_EVERY_RUNNER_ENABLE=0
export PYTHONHASHSEED=0
export KS_NODE=M12
export ATB_STREAM_SYNC_EVERY_KERNEL_ENABLE=0
export ATB_CXX_ABI=1
export ATB_OPSRUNNER_KERNEL_CACHE_GLOABL_COUNT=5
export GLOO_SOCKET_IFNAME=enp23s0f3
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export KS_CONTAINER=ks-pp-m12-priv
export HCCL_BUFFSIZE=256
export ATB_HOME_PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1
export VLLM_ASCEND_ENABLE_FUSED_MC2=1
export CMAKE_PREFIX_PATH=/usr/local/Ascend/cann-9.1.0/toolkit/tools/tikicpulib/lib/cmake:/usr/local/Ascend/cann-9.1.0/lib64/cmake
export TOOLCHAIN_HOME=/usr/local/Ascend/cann-9.1.0/toolkit
export HCCL_OP_EXPANSION_MODE=AIV
export OMP_PROC_BIND=false
export ASCEND_TOOLKIT_HOME=/usr/local/Ascend/cann-9.1.0
export KS_RUN=/opt/ks-metrics-supplement-20261025/work/d1
export PYTHONPATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe:
export LCCL_DETERMINISTIC=0
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_MLAPO=1
export TP_SOCKET_IFNAME=enp23s0f3
export ATB_COMPARE_TILING_EVERY_KERNEL=0
export ASCEND_OPP_PATH=/usr/local/Ascend/cann-9.1.0/opp
export LD_LIBRARY_PATH=/usr/local/Ascend/cann-9.1.0/lib64:/usr/local/Ascend/cann-9.1.0/lib64/plugin/opskernel:/usr/local/Ascend/cann-9.1.0/lib64/plugin/nnengine:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/lib:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/examples:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/tests/atbopstest:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64/plugin:/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/python3.12.13/lib::/usr/local/lib
export LCCL_PARALLEL=0
export SOC_VERSION=ascend910_9391
export ASCEND_AICPU_PATH=/usr/local/Ascend/cann-9.1.0
export OMP_NUM_THREADS=1
export ATB_STREAM_SYNC_EVERY_OPERATION_ENABLE=0
export PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/bin:/usr/local/Ascend/ascend-toolkit/latest/bin:/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/bin:/usr/local/Ascend/ascend-toolkit/latest/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/show_kernel_debug_data:/usr/local/Ascend/cann-9.1.0/tools/msobjdump:/usr/local/Ascend/cann-9.1.0/bin:/usr/local/Ascend/cann-9.1.0/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/profiler/bin:/usr/local/Ascend/cann-9.1.0/tools/ascend_system_advisor/asys:/usr/local/python3.12.13/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ASCEND_HOME_PATH=/usr/local/Ascend/cann-9.1.0
export HCCL_IF_IP=192.168.0.50
export ATB_MATMUL_SHUFFLE_K_ENABLE=1
export ASCEND_A3_ENABLE=1
export DEBIAN_FRONTEND=noninteractive
export ATB_WORKSPACE_MEM_ALLOC_ALG_TYPE=1
export ATB_SHARE_MEMORY_NAME_SUFFIX=''
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/d1/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/work/d1/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/d1/xdg
export HF_HOME=/opt/ks-metrics-supplement-20261025/cache/d1/xdg/huggingface
export PYTHONDONTWRITEBYTECODE=1
mkdir -p /opt/ks-metrics-supplement-20261025/work/d1 "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$HF_HOME"
cd /opt/ks-metrics-supplement-20261025/work/d1
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18201 \
  --data-parallel-size 4 \
  --data-parallel-rank 1 \
  --data-parallel-address 192.168.0.50 \
  --data-parallel-rpc-port 18300 \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --enable-expert-parallel \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 65536 \
  --max-num-batched-tokens 164 \
  --max-num-seqs 32 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":true,"multistream_overlap_shared_expert":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true}' \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --gpu-memory-utilization 0.92 \
  --async-scheduling \
  --quantization ascend \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_consumer","kv_port":"29000","engine_id":"ks-d-off-1","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}}' \
  --kv-cache-memory-bytes 4831838208
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/d/d1.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/d/d1.serve.native.sh`）。

#### 12／D容器：D rank2（端口18202，NPU 8—11）

**运行前环境与工作目录（先在同一终端执行）**

```bash
export ASCEND_RT_VISIBLE_DEVICES=8,9,10,11
export ACL_OP_INIT_MODE=1
export ASCEND_TOOLKIT_LATEST_HOME=/usr/local/Ascend/ascend-toolkit/latest
export HCCL_SOCKET_IFNAME=enp23s0f3
export ATB_OPSRUNNER_KERNEL_CACHE_LOCAL_COUNT=1
export ATB_STREAM_SYNC_EVERY_RUNNER_ENABLE=0
export PYTHONHASHSEED=0
export KS_NODE=M12
export ATB_STREAM_SYNC_EVERY_KERNEL_ENABLE=0
export ATB_CXX_ABI=1
export ATB_OPSRUNNER_KERNEL_CACHE_GLOABL_COUNT=5
export GLOO_SOCKET_IFNAME=enp23s0f3
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export KS_CONTAINER=ks-pp-m12-priv
export HCCL_BUFFSIZE=256
export ATB_HOME_PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1
export VLLM_ASCEND_ENABLE_FUSED_MC2=1
export CMAKE_PREFIX_PATH=/usr/local/Ascend/cann-9.1.0/toolkit/tools/tikicpulib/lib/cmake:/usr/local/Ascend/cann-9.1.0/lib64/cmake
export TOOLCHAIN_HOME=/usr/local/Ascend/cann-9.1.0/toolkit
export HCCL_OP_EXPANSION_MODE=AIV
export OMP_PROC_BIND=false
export ASCEND_TOOLKIT_HOME=/usr/local/Ascend/cann-9.1.0
export KS_RUN=/opt/ks-metrics-supplement-20261025/work/d2
export PYTHONPATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe:
export LCCL_DETERMINISTIC=0
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_MLAPO=1
export TP_SOCKET_IFNAME=enp23s0f3
export ATB_COMPARE_TILING_EVERY_KERNEL=0
export ASCEND_OPP_PATH=/usr/local/Ascend/cann-9.1.0/opp
export LD_LIBRARY_PATH=/usr/local/Ascend/cann-9.1.0/lib64:/usr/local/Ascend/cann-9.1.0/lib64/plugin/opskernel:/usr/local/Ascend/cann-9.1.0/lib64/plugin/nnengine:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/lib:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/examples:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/tests/atbopstest:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64/plugin:/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/python3.12.13/lib::/usr/local/lib
export LCCL_PARALLEL=0
export SOC_VERSION=ascend910_9391
export ASCEND_AICPU_PATH=/usr/local/Ascend/cann-9.1.0
export OMP_NUM_THREADS=1
export ATB_STREAM_SYNC_EVERY_OPERATION_ENABLE=0
export PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/bin:/usr/local/Ascend/ascend-toolkit/latest/bin:/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/bin:/usr/local/Ascend/ascend-toolkit/latest/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/show_kernel_debug_data:/usr/local/Ascend/cann-9.1.0/tools/msobjdump:/usr/local/Ascend/cann-9.1.0/bin:/usr/local/Ascend/cann-9.1.0/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/profiler/bin:/usr/local/Ascend/cann-9.1.0/tools/ascend_system_advisor/asys:/usr/local/python3.12.13/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ASCEND_HOME_PATH=/usr/local/Ascend/cann-9.1.0
export HCCL_IF_IP=192.168.0.50
export ATB_MATMUL_SHUFFLE_K_ENABLE=1
export ASCEND_A3_ENABLE=1
export DEBIAN_FRONTEND=noninteractive
export ATB_WORKSPACE_MEM_ALLOC_ALG_TYPE=1
export ATB_SHARE_MEMORY_NAME_SUFFIX=''
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/d2/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/work/d2/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/d2/xdg
export HF_HOME=/opt/ks-metrics-supplement-20261025/cache/d2/xdg/huggingface
export PYTHONDONTWRITEBYTECODE=1
mkdir -p /opt/ks-metrics-supplement-20261025/work/d2 "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$HF_HOME"
cd /opt/ks-metrics-supplement-20261025/work/d2
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18202 \
  --data-parallel-size 4 \
  --data-parallel-rank 2 \
  --data-parallel-address 192.168.0.50 \
  --data-parallel-rpc-port 18300 \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --enable-expert-parallel \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 65536 \
  --max-num-batched-tokens 164 \
  --max-num-seqs 32 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":true,"multistream_overlap_shared_expert":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true}' \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --gpu-memory-utilization 0.92 \
  --async-scheduling \
  --quantization ascend \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_consumer","kv_port":"29000","engine_id":"ks-d-off-2","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}}' \
  --kv-cache-memory-bytes 4831838208
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/d/d2.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/d/d2.serve.native.sh`）。

#### 12／D容器：D rank3（端口18203，NPU 12—15）

**运行前环境与工作目录（先在同一终端执行）**

```bash
export ASCEND_RT_VISIBLE_DEVICES=12,13,14,15
export ACL_OP_INIT_MODE=1
export ASCEND_TOOLKIT_LATEST_HOME=/usr/local/Ascend/ascend-toolkit/latest
export HCCL_SOCKET_IFNAME=enp23s0f3
export ATB_OPSRUNNER_KERNEL_CACHE_LOCAL_COUNT=1
export ATB_STREAM_SYNC_EVERY_RUNNER_ENABLE=0
export PYTHONHASHSEED=0
export KS_NODE=M12
export ATB_STREAM_SYNC_EVERY_KERNEL_ENABLE=0
export ATB_CXX_ABI=1
export ATB_OPSRUNNER_KERNEL_CACHE_GLOABL_COUNT=5
export GLOO_SOCKET_IFNAME=enp23s0f3
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export KS_CONTAINER=ks-pp-m12-priv
export HCCL_BUFFSIZE=256
export ATB_HOME_PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1
export VLLM_ASCEND_ENABLE_FUSED_MC2=1
export CMAKE_PREFIX_PATH=/usr/local/Ascend/cann-9.1.0/toolkit/tools/tikicpulib/lib/cmake:/usr/local/Ascend/cann-9.1.0/lib64/cmake
export TOOLCHAIN_HOME=/usr/local/Ascend/cann-9.1.0/toolkit
export HCCL_OP_EXPANSION_MODE=AIV
export OMP_PROC_BIND=false
export ASCEND_TOOLKIT_HOME=/usr/local/Ascend/cann-9.1.0
export KS_RUN=/opt/ks-metrics-supplement-20261025/work/d3
export PYTHONPATH=/usr/local/Ascend/ascend-toolkit/latest/python/site-packages:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe:/usr/local/Ascend/cann-9.1.0/python/site-packages:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe:
export LCCL_DETERMINISTIC=0
export TASK_QUEUE_ENABLE=1
export VLLM_ASCEND_ENABLE_MLAPO=1
export TP_SOCKET_IFNAME=enp23s0f3
export ATB_COMPARE_TILING_EVERY_KERNEL=0
export ASCEND_OPP_PATH=/usr/local/Ascend/cann-9.1.0/opp
export LD_LIBRARY_PATH=/usr/local/Ascend/cann-9.1.0/lib64:/usr/local/Ascend/cann-9.1.0/lib64/plugin/opskernel:/usr/local/Ascend/cann-9.1.0/lib64/plugin/nnengine:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/lib:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/examples:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/tests/atbopstest:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64/plugin:/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/python3.12.13/lib::/usr/local/lib
export LCCL_PARALLEL=0
export SOC_VERSION=ascend910_9391
export ASCEND_AICPU_PATH=/usr/local/Ascend/cann-9.1.0
export OMP_NUM_THREADS=1
export ATB_STREAM_SYNC_EVERY_OPERATION_ENABLE=0
export PATH=/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/bin:/usr/local/Ascend/ascend-toolkit/latest/bin:/usr/local/Ascend/ascend-toolkit/latest/compiler/ccec_compiler/bin:/usr/local/Ascend/ascend-toolkit/latest/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/show_kernel_debug_data:/usr/local/Ascend/cann-9.1.0/tools/msobjdump:/usr/local/Ascend/cann-9.1.0/bin:/usr/local/Ascend/cann-9.1.0/tools/ccec_compiler/bin:/usr/local/Ascend/cann-9.1.0/tools/profiler/bin:/usr/local/Ascend/cann-9.1.0/tools/ascend_system_advisor/asys:/usr/local/python3.12.13/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export ASCEND_HOME_PATH=/usr/local/Ascend/cann-9.1.0
export HCCL_IF_IP=192.168.0.50
export ATB_MATMUL_SHUFFLE_K_ENABLE=1
export ASCEND_A3_ENABLE=1
export DEBIAN_FRONTEND=noninteractive
export ATB_WORKSPACE_MEM_ALLOC_ALG_TYPE=1
export ATB_SHARE_MEMORY_NAME_SUFFIX=''
export KS_WORK_HOME=/opt/ks-metrics-supplement-20261025/cache/d3/home
export TMPDIR=/opt/ks-metrics-supplement-20261025/work/d3/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/cache/d3/xdg
export HF_HOME=/opt/ks-metrics-supplement-20261025/cache/d3/xdg/huggingface
export PYTHONDONTWRITEBYTECODE=1
mkdir -p /opt/ks-metrics-supplement-20261025/work/d3 "$KS_WORK_HOME" "$TMPDIR" "$XDG_CACHE_HOME" "$HF_HOME"
cd /opt/ks-metrics-supplement-20261025/work/d3
```

```bash
vllm serve /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --host 0.0.0.0 \
  --port 18203 \
  --data-parallel-size 4 \
  --data-parallel-rank 3 \
  --data-parallel-address 192.168.0.50 \
  --data-parallel-rpc-port 18300 \
  --tensor-parallel-size 4 \
  --pipeline-parallel-size 1 \
  --enable-expert-parallel \
  --hf-overrides '{"use_index_cache":true,"index_topk_freq":4}' \
  --seed 1024 \
  --served-model-name glm-5 \
  --max-model-len 65536 \
  --max-num-batched-tokens 164 \
  --max-num-seqs 32 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative-config '{"num_speculative_tokens":3,"method":"deepseek_mtp","enforce_eager":true}' \
  --additional-config '{"fuse_muls_add":true,"recompute_scheduler_enable":true,"multistream_overlap_shared_expert":true,"enable_sparse_sfa_c8":true,"enable_sparse_li_c8":true}' \
  --trust-remote-code \
  --no-enable-prefix-caching \
  --gpu-memory-utilization 0.92 \
  --async-scheduling \
  --quantization ascend \
  --enable-auto-tool-choice \
  --tool-call-parser glm47 \
  --reasoning-parser glm45 \
  --kv-transfer-config '{"kv_connector":"MooncakeConnectorV1","kv_role":"kv_consumer","kv_port":"29000","engine_id":"ks-d-off-3","kv_connector_extra_config":{"use_ascend_direct":true,"prefill":{"dp_size":1,"tp_size":8,"pp_size":2,"pp_layer_partition":"41,37"},"decode":{"dp_size":4,"tp_size":4,"pp_size":1}}}' \
  --kv-cache-memory-bytes 4831838208
```

来源：归档环境（`../../20261025_性能指标补采/原生复现/d/d3.env.native.sh`）；归档命令（`../../20261025_性能指标补采/原生复现/d/d3.serve.native.sh`）。

#### 12／D容器：PD代理

这是现有代理的原生入口；初始化逻辑位于其主入口，保留Python服务命令。

```bash
set -e
export HOME=/opt/ks-m8p-m12d-luv-20261023/runtime/proxy/home
export TMPDIR=/opt/ks-m8p-m12d-luv-20261023/runtime/proxy/tmp
export XDG_CACHE_HOME=/opt/ks-m8p-m12d-luv-20261023/runtime/proxy/cache
export HF_HOME="$XDG_CACHE_HOME/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HUGGINGFACE_HUB_CACHE="$HF_HUB_CACHE"
export VLLM_CACHE_ROOT="$XDG_CACHE_HOME/vllm"
export PYTHONDONTWRITEBYTECODE=1
export NO_PROXY=127.0.0.1,localhost,192.168.0.110,192.168.0.50
export no_proxy="$NO_PROXY"
unset PYTHONPATH TRANSFORMERS_CACHE
mkdir -p "$HOME" "$TMPDIR" "$XDG_CACHE_HOME"
cd /opt/ks-m8p-m12d-luv-20261023/bench
/usr/local/python3.12.13/bin/python3 proxy_v023.py \
  --host 127.0.0.1 --port 18080 --workers 1 --max-retries 1 \
  --prefiller-hosts 192.168.0.110 --prefiller-ports 18100 \
  --decoder-hosts 192.168.0.50 192.168.0.50 192.168.0.50 192.168.0.50 \
  --decoder-ports 18200 18201 18202 18203
```

来源：代理命令原件（`../../01_环境与原生启动配置/8作P与12作D静态准备_20261023/bench/proxy-m12.native.sh`）。

#### 12／D容器：客户原文最长请求容量诊断

正式轮之前单独执行1条最长客户请求，验证容量；该条不计入389条正式结果。

**benchmark运行环境（诊断与正式轮均先在该终端执行）**

```bash
unset PYTHONPATH KS_BENCH_INPUT_LOG
export PYTHONDONTWRITEBYTECODE=1
export TMPDIR=/opt/ks-metrics-supplement-20261025/benchmark/tmp
export XDG_CACHE_HOME=/opt/ks-metrics-supplement-20261025/benchmark/cache
export HF_HOME="$XDG_CACHE_HOME/huggingface"
export VLLM_CACHE_ROOT="$XDG_CACHE_HOME/vllm"
export NO_PROXY=127.0.0.1,localhost,192.168.0.110,192.168.0.50
export no_proxy="$NO_PROXY"
mkdir -p "$TMPDIR" "$XDG_CACHE_HOME" /opt/ks-metrics-supplement-20261025/stages/on-async/results
cd /opt/ks-metrics-supplement-20261025/benchmark
```

```bash
vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:18080 \
  --endpoint /v1/completions \
  --model glm-5 \
  --tokenizer /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --dataset-name custom \
  --dataset-path /opt/ks-customer-bench-20261024/benchmark/capacity-longest-49152.jsonl \
  --custom-output-len -1 \
  --disable-shuffle \
  --skip-chat-template \
  --no-oversample \
  --num-prompts 1 \
  --max-concurrency 1 \
  --request-rate inf \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --temperature 0 \
  --ignore-eos \
  --seed 0 \
  --percentile-metrics ttft,tpot,itl,e2el \
  --metric-percentiles 50,90,95,99 \
  --save-result \
  --save-detailed \
  --result-dir /opt/ks-metrics-supplement-20261025/stages/on-async/diagnostic \
  --result-filename benchmark.json
```

#### 8／P容器：正式轮开始前的缓存初态

最长诊断后，实际控制程序在无在途业务时调用非强制清理，再两次验空。下面是同一HTTP操作的手动形式；检查`/status`中对象数／已用字节为0且无在途任务后，再开始正式轮。

```bash
curl --noproxy '*' -sS http://127.0.0.1:15565/status
curl --noproxy '*' -sS -X POST http://127.0.0.1:15565/cache/clear \
  -H 'Content-Type: application/json' \
  -d '{"tier":"l1","force":false}'
curl --noproxy '*' -sS http://127.0.0.1:15565/status
curl --noproxy '*' -sS http://127.0.0.1:15565/status
```

#### 12／D容器：正式benchmark（389条客户原文）

```bash
vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:18080 \
  --endpoint /v1/completions \
  --model glm-5 \
  --tokenizer /mnt/sdb/models/GLM-5.1-w8a8c8-MTP \
  --dataset-name custom \
  --dataset-path /opt/ks-customer-bench-20261024/capacity-pools-20261024T110510324465Z/cap_49152/custom.jsonl \
  --custom-output-len -1 \
  --disable-shuffle \
  --skip-chat-template \
  --no-oversample \
  --num-prompts 389 \
  --max-concurrency 4 \
  --request-rate inf \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --temperature 0 \
  --ignore-eos \
  --seed 0 \
  --percentile-metrics ttft,tpot,itl,e2el \
  --metric-percentiles 50,90,95,99 \
  --save-result \
  --save-detailed \
  --result-dir /opt/ks-metrics-supplement-20261025/stages/on-async/results \
  --result-filename benchmark.json
```

上面的 `vllm bench serve` 是与实际请求参数一致的原生命令。10/25采集时用已有 `client_observe.py bench serve` 调用同一benchmark，额外记录逐请求时间；直接运行原生命令不会产生同样的 `client.jsonl`。原采集参数保留不改，本报告不展示流块指标。

完整原生与观测入口记录：实际benchmark启动回执（`../../20261025_性能指标补采/KV动作聚合/on-async/正式元数据/stages/on-async/results/launch.json`）。两组请求参数相同，只有结果目录按组区分；结果路径是当时归档路径，重跑时须另选结果目录，避免覆盖旧结果。

## 六、结果与配置原件

| 材料 | 归档入口 |
| --- | --- |
| OFF官方原始结果 | benchmark.json（`../../20261025_性能指标补采/分析/正式指标/off/输入/m12-20261025-124214-243081/stages/off/results/benchmark.json`） |
| ON官方原始结果 | benchmark.json（`../../20261025_性能指标补采/分析/正式指标/on-async/输入/m12-20261025-131405-170366/stages/on-async/results/benchmark.json`） |
| 两组完整指标与来源 | report_metrics.json（本报告仅取customer_off、customer_async）（`../../20261025_性能指标补采/分析/正式指标/report_metrics.json`） |
| 10/25启动环境与命令 | P OFF命令（`../../20261025_性能指标补采/原生复现/p/off.serve.native.sh`）；P ON命令（`../../20261025_性能指标补采/原生复现/p/on-async.serve.native.sh`）；LMCache命令（`../../20261025_性能指标补采/原生复现/cache/server.shared.native.sh`） |
| 四D实际进程参数回执 | 12服务器归档状态（`../../20261025_性能指标补采/运行回执/20261025-121813-840481-m12-status.json`） |
| 接入代码与观测环境来源 | runtime来源清单（`../启动配置/runtime来源清单.md`） |
| 本报告本地生成入口 | 生成终版测试报告.py（`../../20261025_性能指标补采/分析/六个关键问题复盘/生成终版测试报告.py`） |

原始JSON、脚本与归档记录保持不变；报告的范围筛选不等于删除历史实验原件。
