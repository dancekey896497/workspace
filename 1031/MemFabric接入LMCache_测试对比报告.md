# LMCache接入MemFabric：L2受控条件性能对比汇报

<p class="correction"><strong>主结论适用前提：这是人为增强读池的受控实验，并非自然输入／默认完整服务测试。</strong><br>所有组都设置“写池后删除CPU L1副本”，并关闭模型本地前缀缓存，增加了对L2池的依赖，用于放大、观察两种L2后端的性能差异；不能把这些提升率当作自然运行收益。严格组还额外等待写池和CPU清理完成再发D，等待包含在TTFT中。输入为自行生成的裁剪文本，不是客户原始JSONL。相对自然条件究竟放大了多少，尚无同配置对照可量化。</p>


实测：2026-10-29，单机A3／Qwen3-8B／TP1／1P1D。对照：Mooncake Store L2 vs MemFabric L2。

**怎么改的：** 新增`MemFabricL2Adapter`，把LMCache service的L2读写接口适配到MemFabric BM；与Mooncake Store L2对照。模型和P／D接入参数保持一致，数据仍走`NPU ↔ CPU L1 ↔ L2池`。

**对比结果：** 在两后端都“等写完再交接”的受控实验中，三档合计平均TTFT **1,771.3 → 946.9 ms，下降46.54%**；输出吞吐 **15.40 → 17.20 token/s，提高11.66%**；平均TPOT **51.48 → 51.14 ms/token，基本持平**。这不是默认运行条件下的结论。

**优势在哪：** 主要观察到**L2读入CPU L1、准备KV的等待更短**；本轮两组共用后续H2D实现，没有证明NPU搬运或生成本身更快。

本轮属于诊断结果：受控组每档8请求、单轮；退出异常及基础两处文本差异尚未闭环。[完整证据与限制见附录](#appendix)。

## 1. 启动用例参数差异

| 启动项 | Mooncake Store L2 | MemFabric L2 |
| --- | --- | --- |
| service的`--l2-adapter` | `type=mooncake_store`，使用Store SDK | `type=plugin`，加载新增`MemFabricL2Adapter` |
| 池端 | Store master＋独立owner | BM member；`world_size=2`、`storage_rank=1` |
| 名义池参数（长输入用例） | owner 64GiB；client段16MiB | rank段64GiB；slot32MiB、对象payload18MiB |
| 数据面 | TENT／AscendDirect高速路径 | MemFabric BM／SDMA |
| 两组相同的P／D参数 | TP1、BF16、eager；`max-num-seqs=4`；`max-num-batched-tokens=1024`；显存利用率0.7 | 相同 |
| 两组相同的service策略 | chunk128、LRU；写池成功后删除CPU L1副本（`skip_l1`） | 相同 |

**主要变化在L2后端与必要池端进程。** 名义64GiB不等于实际有效KV容量完全相同；完整原生命令、配置及差异在附录。

## 2. 设备环境和服务用例

| 项目 | 本轮设置 |
| --- | --- |
| 服务器／设备 | 单机A3：115.120.47.8；P=8、D=10、service=12、池端=14 |
| 模型／服务 | Qwen3-8B，1P1D，P／D各TP1；两组均保留LMCache |
| 软件基线 | LMCache core `6913259`；LMCache-Ascend `525a710`；MemFabric 1.2.1；Store SDK 0.3.11.post1 |
| 容量／CPU | 长输入：上下文32768、CPU L1 8GiB、名义池64GiB；基础：4096／4GiB／4GiB；CPU affinity96—119 |
| 本轮验证范围 | L2兼容路径，共688条计时请求、0请求失败；“等写完再交接”组96条预取日志全部零L1命中 |

## 3. 测试数据集和用例差异

数据为经Qwen分词器校验的合成文本，沿用客户用例的长短输入、公共前缀和长短输出结构；公共前缀比例不是实测命中率。

[六份启动配置核查](method_audit.json)。**输入长度、公共前缀和输出长度才是数据用例参数；下表的分组还包含实验运行方式，不是三种请求状态。** 两种交接组都启用了“写池后删除CPU副本”，区别是是否额外等待后台工作做完。代理读取状态来决定何时发送D，**没有把监控计数器手工清零**；额外等待也计入TTFT。

| 实验组（运行方式） | 输入／公共前缀／输出token | 请求数／并发 | 怎么跑、看什么 |
| --- | --- | --- | --- |
| **等写完再交接（受控主对照）** | 4096／2048／128；16384／8192／128；16384／14720／128 | 每档8条、合计24条/后端；C1 | 等KV写入池、CPU副本按既定策略删完，再让后半段继续生成，避免直接用到还没删掉的CPU副本。 |
| 重复前缀组 | 1151／1024／32；3199／3072／32（C1、C4） | 每档3×32条、合计288条/后端 | 先把可复用的KV存好，再反复发送开头相同的请求，比较重复读取这些KV时的表现。 |
| 原交接流程 | 与第一组相同的三档＋16384／14720／1000 | 每档8条、合计32条/后端；C1 | 不额外等后台写池和CPU副本清理做完就继续接力，可能用到尚未删除的CPU副本；这组也配置了写池后删除CPU副本，并非默认缓存策略。 |

## 4. 关键指标 × 用例 × 对比结果

变化方向均为**Mooncake Store → MemFabric**。耗时越低越好，吞吐越高越好。

<table class="comparison main-results"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">等写完再交接｜24请求/后端</th><th colspan="3" scope="colgroup">重复前缀组｜288请求/后端</th><th colspan="3" scope="colgroup">原交接流程｜32请求/后端</th></tr><tr><th scope="col">Mooncake Store</th><th scope="col">MemFabric</th><th scope="col">变化结果</th><th scope="col">Mooncake Store</th><th scope="col">MemFabric</th><th scope="col">变化结果</th><th scope="col">Mooncake Store</th><th scope="col">MemFabric</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">平均TTFT（ms）</th><td>1,771.27</td><td>946.87</td><td>下降46.54%（改善）</td><td>360.44</td><td>325.29</td><td>下降9.75%（改善）</td><td>1,341.83</td><td>864.82</td><td>下降35.55%（改善）</td></tr><tr><th scope="row">平均TPOT（ms/token）</th><td>51.48</td><td>51.14</td><td>下降0.66%（改善）</td><td>52.38</td><td>48.08</td><td>下降8.21%（改善）</td><td>50.15</td><td>48.42</td><td>下降3.45%（改善）</td></tr><tr><th scope="row">平均端到端时延（ms）</th><td>8,308.86</td><td>7,441.27</td><td>下降10.44%（改善）</td><td>1,984.17</td><td>1,815.66</td><td>下降8.49%（改善）</td><td>19,188.63</td><td>17,975.43</td><td>下降6.32%（改善）</td></tr><tr><th scope="row">输入吞吐（token/s）</th><td>1,478.81</td><td>1,651.22</td><td>增加11.66%（改善）</td><td>1,798.21</td><td>1,962.20</td><td>增加9.12%（改善）</td><td>693.72</td><td>740.55</td><td>增加6.75%（改善）</td></tr><tr><th scope="row">输出吞吐（token/s）</th><td>15.40</td><td>17.20</td><td>增加11.66%（改善）</td><td>22.87</td><td>24.95</td><td>增加9.12%（改善）</td><td>18.03</td><td>19.25</td><td>增加6.75%（改善）</td></tr><tr><th scope="row">请求吞吐（req/s）</th><td>0.120</td><td>0.134</td><td>增加11.66%（改善）</td><td>0.715</td><td>0.780</td><td>增加9.12%（改善）</td><td>0.052</td><td>0.056</td><td>增加6.75%（改善）</td></tr><tr><th scope="row">整轮时长（s）</th><td>199.43</td><td>178.60</td><td>下降10.44%（改善）</td><td>403.01</td><td>369.33</td><td>下降8.36%（改善）</td><td>614.05</td><td>575.23</td><td>下降6.32%（改善）</td></tr></tbody></table>


**汇总口径：** 各集合单独汇总；延迟按请求数加权，输入／输出／请求吞吐=相应总数÷各档计时时长之和，整轮时长为各档计时合计（不含seed及档间空隙）。输入吞吐包含复用token，不是NPU实际计算速率。基础含不同并发，自然含不同输出长度，集合间不能当作容量高低比较。

**分档趋势（等写完再交接组）：** 从4K／50%前缀，到16K／50%，再到16K／89.84%，首字等待分别节省 **291 → 966 → 1217 ms**；TTFT降幅分别为 **38.76% → 38.72% → 58.79%**，输出吞吐增幅分别为 **8.95% → 11.30% → 14.58%**。本轮长输入、高前缀复用档的整体收益更明显。

**不是“读池越多，收益比例一定越大”。** 前两档TTFT降幅都约38.7%；后两档D端均恢复128个L2对象，P端分别复用64／115个对象，重算与新增写入也不同。现有结果支持“KV准备在请求中的占比越高，缩短这段等待越有价值”，尚未证明收益与读量成正比。TPOT也未同步改善：4K降低4.82%，16K两档反而增加0.87%／2.19%。

<div class="trend-grid"><figure class="trend-chart"><figcaption>等写完再交接：TTFT降幅（越高越好）</figcaption><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 560 235" role="img" aria-label="等写完再交接：TTFT降幅（越高越好）"><line x1="52" x2="537" y1="180.00" y2="180.00" stroke="#dce5de"/><text x="42" y="184.00" text-anchor="end" font-size="12" fill="#52646a">0%</text><line x1="52" x2="537" y1="141.43" y2="141.43" stroke="#dce5de"/><text x="42" y="145.43" text-anchor="end" font-size="12" fill="#52646a">20%</text><line x1="52" x2="537" y1="102.86" y2="102.86" stroke="#dce5de"/><text x="42" y="106.86" text-anchor="end" font-size="12" fill="#52646a">40%</text><line x1="52" x2="537" y1="64.29" y2="64.29" stroke="#dce5de"/><text x="42" y="68.29" text-anchor="end" font-size="12" fill="#52646a">60%</text><rect x="86" y="105.25" width="68" height="74.75" rx="4" fill="#177361"/><text x="120" y="96.25" text-anchor="middle" font-size="15" font-weight="600" fill="#177361">38.76%</text><text x="120" y="207" text-anchor="middle" font-size="13" fill="#20353b">4K／50%前缀</text><rect x="256" y="105.33" width="68" height="74.67" rx="4" fill="#177361"/><text x="290" y="96.33" text-anchor="middle" font-size="15" font-weight="600" fill="#177361">38.72%</text><text x="290" y="207" text-anchor="middle" font-size="13" fill="#20353b">16K／50%前缀</text><rect x="426" y="66.62" width="68" height="113.38" rx="4" fill="#177361"/><text x="460" y="57.62" text-anchor="middle" font-size="15" font-weight="600" fill="#177361">58.79%</text><text x="460" y="207" text-anchor="middle" font-size="13" fill="#20353b">16K／89.84%前缀</text></svg></figure><figure class="trend-chart"><figcaption>等写完再交接：输出吞吐增幅（越高越好）</figcaption><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 560 235" role="img" aria-label="等写完再交接：输出吞吐增幅（越高越好）"><line x1="52" x2="537" y1="180.00" y2="180.00" stroke="#dce5de"/><text x="42" y="184.00" text-anchor="end" font-size="12" fill="#52646a">0%</text><line x1="52" x2="537" y1="142.50" y2="142.50" stroke="#dce5de"/><text x="42" y="146.50" text-anchor="end" font-size="12" fill="#52646a">5%</text><line x1="52" x2="537" y1="105.00" y2="105.00" stroke="#dce5de"/><text x="42" y="109.00" text-anchor="end" font-size="12" fill="#52646a">10%</text><line x1="52" x2="537" y1="67.50" y2="67.50" stroke="#dce5de"/><text x="42" y="71.50" text-anchor="end" font-size="12" fill="#52646a">15%</text><rect x="86" y="112.89" width="68" height="67.11" rx="4" fill="#a26628"/><text x="120" y="103.89" text-anchor="middle" font-size="15" font-weight="600" fill="#a26628">8.95%</text><text x="120" y="207" text-anchor="middle" font-size="13" fill="#20353b">4K／50%前缀</text><rect x="256" y="95.27" width="68" height="84.73" rx="4" fill="#a26628"/><text x="290" y="86.27" text-anchor="middle" font-size="15" font-weight="600" fill="#a26628">11.30%</text><text x="290" y="207" text-anchor="middle" font-size="13" fill="#20353b">16K／50%前缀</text><rect x="426" y="70.64" width="68" height="109.36" rx="4" fill="#a26628"/><text x="460" y="61.64" text-anchor="middle" font-size="15" font-weight="600" fill="#a26628">14.58%</text><text x="460" y="207" text-anchor="middle" font-size="13" fill="#20353b">16K／89.84%前缀</text></svg></figure></div>

图中横轴是三个不同用例，不是等间距读量；两图纵轴分别标注比例，不能用柱高直接比较两种指标。各档完整数值与负向变化保留在附录。

[展开附录：各档详细表、完整命令、预取解释、机理及未解决项](#appendix)

<details id="appendix">
<summary>附录｜受控实验的完整原件、计时边界及方法核查（点击展开）</summary>

> **历史诊断资料：** 以下原结果与旧分析用于追溯，不是客户原始数据／默认完整服务的正式性能结论。数值与原件保留；其中“严格组”的提升率及机制归因不能推广到正常服务。


<p class="correction"><strong>结论更正：</strong>此前首页的“TTFT下降46.54%、吞吐提高11.66%”来自受控读池实验，现撤回作为“客户用例／默认完整服务性能收益”的结论。当前没有完成符合这一目标口径的性能A/B验证；旧数据全部保留为附录中的诊断记录。</p>

**改了什么：** 新增`MemFabricL2Adapter`，将LMCache的L2读写接口适配到MemFabric BM，已有池化存取与KV恢复功能证据。**还不能汇报什么：** 客户规定数据集、正常服务流程下的TTFT／TPOT／吞吐收益，以及默认服务的收益来源。

## 1. 启动用例参数差异

以下是对历史启动配置的核查结果。两后端使用相同实验控制，并不等于已经按默认服务方法完成测试。

| 历史实验集合 | 写池后删除CPU L1副本 | 模型本地前缀缓存 | 是否额外等待再发D |
| --- | --- | --- | --- |
| 基础重复前缀，2个后端 | 均显式设置`skip_l1` | 均显式关闭 | 否，使用原代理流程 |
| 原交接流程，2个后端 | 均显式设置`skip_l1` | 均显式关闭 | 否，使用原代理流程 |
| 原“严格组”，2个后端 | 均显式设置`skip_l1` | 均显式关闭 | **是，新增实验代理等待** |

`skip_l1`会在成功写池后删除L1副本；本轮core的默认策略则保留L1副本。严格组还等后台写入、清理等工作结束才发D，等待包含在客户端TTFT中。代理只读取`/status`，没有手工清零监控值；但服务行为和请求时序确实受到控制，不能说只是换了观察点。[逐配置核查](method_audit.json)

## 2. 设备环境和服务用例

| 项目 | 已有记录／当前边界 |
| --- | --- |
| 设备与模型 | 2026-10-29，单机A3（115.120.47.8），Qwen3-8B，TP1／1P1D；P=8、D=10、service=12、池端=14 |
| 对照对象 | Mooncake Store L2 vs MemFabric L2；两组均保留LMCache；数据经过CPU L1中转 |
| 已有材料 | 688条计时请求及功能、读池来源、输出与退出记录；属于上述实验配置下的诊断 |
| 尚未完成 | 客户规定方法／正常服务条件下，只改变数据用例的正式性能对照 |

## 3. 测试数据集和用例差异

**没有用客户原始JSONL跑出这批结果。** 实际材料是根据长度、公共前缀、输出长度自行构造并经Qwen分词器校验的文本。允许裁剪用例，不等于这些合成数据可以称为客户原始数据，也不等于可以把额外服务控制归入“数据差异”。

| 材料 | 实际情况 |
| --- | --- |
| 客户长上下文说明 | 128000／190000／198000输入、1000输出等五档；原始百分比定义未核实 |
| 本次基础材料 | 合成1151／3199输入、32输出、C1／C4，固定公共前缀工作集 |
| 本次长短材料 | 合成4096／16384输入、128／1000输出；“约90%”按公共前缀比例构造 |
| 方法差异 | 所有组均控制缓存策略；严格组另控制交接等待。这些都不是数据集本身的变化 |

## 4. 关键指标 × 用例 × 对比结果

| 指标 | 客户规定数据／正常完整服务下的Mooncake Store与MemFabric对照 |
| --- | --- |
| TTFT、TPOT、平均端到端时延 | **尚未验证** |
| 输入／输出／请求吞吐、整轮时长 | **尚未验证** |
| 默认服务的优势环节与随输入变化的趋势 | **尚不能下结论**；不能直接沿用受控组的提升率和趋势图 |

**下一轮的正确口径：** 固定同一模型、容量和完整服务方法，采用同一份明确来源的数据及请求参数；两组仅保留L2后端所必需的配置差异。数据档位只改变输入长度、公共前缀、输出长度或并发。客户端按正常请求全过程采集TTFT／TPOT／吞吐，日志被动核对实际缓存命中；不为强制读池添加等待或临时清缓存。读池专用诊断另列，不进入正式服务性能表。

[展开附录：旧诊断数值、实际命令与核查依据（不作为客户正式性能结果）](#appendix)




整理日期：2026年10月30日。主性能结果采用**2026年10月29日M8、Qwen3-8B／TP1／1P1D的兼容L2对照**；137的L2 API与独立BM／classic TE结果仅作机理辅助。本文不是LMCache OFF／ON，也不将worker直传或跨机新功能填入旧性能表。

文档组织参照用户指定的《PP2接入LMCache_终版测试报告.md》，只复用其报告格式，不使用其中GLM、389请求、PP2或OFF／ON性能数值。本次仅整理本地归档，没有新增压测、改生产源码、启停容器或占卡。

## 一、结论

在相同Qwen3-8B、TP1／1P1D、输入输出与P／D执行参数下，本次严格读池三档中，MemFabric L2相对Mooncake Store L2的平均TTFT下降**38.72%—58.79%**，输出吞吐提高**8.95%—14.58%**。TPOT未一致改善：4K档下降4.82%，16K两档分别增加0.87%／2.19%，变差值保留。每档每后端8请求、单轮；正文汇总本次固定用例集合，分档结果在附录，不保证多轮稳定收益或其他负载下相同提升。

两组共用CPU L1→NPU的H2D实现与对应P／D参数。当前差异定位在L2读入CPU L1的综合流程，而不是已证明H2D更快。137代表样本进一步显示主要可见耗时落在SDK／同步API调用区间，不能将收益简单归因于“少一层RPC”或“BM硬件带宽更高”。独立classic TE搬运中，TE高速路径在18／64MiB还快于本次BM配置，比较层级不可混淆。

**验收边界：** 全部688条计时请求无请求失败，新增客户结构裁剪56对文本相等；基础仍有2处句点差异，服务链及Store存在异常退出，完整生命周期验收未通过，性能保持诊断结论。后续独立worker关闭修复与跨机DEVICE池功能另有验证，不回写本轮历史退出或性能结果。

## 二、验证条件和客户数据

| 项目 | 两组共同条件 |
| --- | --- |
| 主性能机器／容器 | 115.120.47.8（M8）／memfabric-d00896497-478-l2ab-20261028 |
| 角色设备 | P物理8；D物理10；service transport物理12；池member／Store owner物理14 |
| 模型 | 本地Qwen3-8B，BF16；只读复用原模型，不修改config |
| 主版本 | LMCache core 6913259／LMCache-Ascend 525a710兼容L2；不混入worker-direct |
| 搬运／SDK | MemFabric 1.2.1；Store SDK 0.3.11.post1；沿用归档私有native桥接与环境，不称纯官方零补丁基线 |
| 镜像记录 | quay.io/ascend/vllm-ascend:v0.23.0-a3；镜像标签不替代精确vLLM源码commit证明 |
| P／D计算形态 | 各TP1，1P1D；seed1024、block128、eager；vLLM本地prefix caching关闭 |
| P／D执行参数 | max-num-seqs=4；max-num-batched-tokens=1024；gpu-memory-utilization=0.7 |
| Connector／传输 | 两组P／D均LMCacheMPConnector，lmcache_driven；共同NPU↔CPU L1暂存段 |
| 主三档容量 | max-model-len=32768；CPU L1=8GiB；名义远端池64GiB；基础档为4096／4GiB／4GiB |
| CPU | 记录96—119 affinity、OMP_NUM_THREADS=1；不代表总线程数或实际NUMA页已完全匹配 |
| 数据 | 合成tokenizer校验文本，保留客户“长短输入／公共前缀／短长输出”的结构；不是客户原文 |
| 压测共同设置 | 官方vllm bench serve，custom数据；request-rate=inf，temperature=0，ignore-eos，disable-shuffle，no-oversample，seed0，skip-chat-template |
| 缓存来源 | 基础、严格三档逐次核对全L2；自然4K例外单独标注，不以L1前后快照替代来源检查 |
| 统计范围 | 基础576＋自然64＋严格48＝688计时请求，0失败；seed／warmup单列；不删除长尾 |

### 两组的接入差异

| 项目 | Mooncake Store L2 | MemFabric L2 |
| --- | --- | --- |
| service后端选择 | mooncake_store，现有native connector及SDK | plugin，新增MemFabricL2Adapter，经BMSession调用BM |
| 对象与地址管理 | Store对象、位置及副本描述符、allocator与SDK流程 | adapter本地key→slot、layout、pin及LRU；BM给池地址 |
| 实际数据面 | TENT／AscendDirectTransport；LocalCommRes1.2、BufferPool4:8 | BM／SDMA，service rank0＋storage rank1 |
| CPU L1→NPU | 公共LMCache／Ascend H2D实现 | 相同；本轮没有NPU worker直传 |
| 池端进程 | master＋独立owner | BM pool member |
| 容量差异 | owner64GiB、client段16MiB；client local buffer256MiB、owner local buffer64MiB | 两rank各声明64GiB，仅rank1存对象；slot32MiB／payload18MiB，理想有效payload约36GiB |
| 控制变量边界 | 保留Store线程／分配器／对象实现 | 保留BM池与adapter实现；不是只换copy函数 |

Store配置的protocol=tcp仅是原始配置字段；运行日志确认高速TENT／AscendDirect路径，不能称为普通TCP低速基线。名义池容量相近不等于总保留内存、有效容量或NUMA一致。没有引入MemCache。

### 数据和运行协议

| 协议 | 输入／公共前缀／输出／并发 | 正式量／预置与预热 | 运行顺序与边界 |
| --- | --- | --- | --- |
| basic，基础warm-read | 1151／1024／32／C1；3199／3072／32／C1、C4 | 每后端每条件3×32；4组前缀、seed4；每轮外置warmup4 | 完整tail127不新增完整chunk；18轮576请求，预取1152条全部零L1 |
| customer01，自然交接 | 4096／2048／128／C1；16384／8192／128／C1；16384／14720／128或1000／C1 | 每后端每档seed2（输出1）＋timed8，无额外warmup | MF→Store；长tail新增写池；Store4K的D为8 L1＋24 L2，其余全L2 |
| customer02_strict，严格读池 | 前三个128输出档，同dataset、新进程和新池 | 每后端每档seed2（输出1）＋timed8，无额外warmup | Store→MF；原P事件后proxy等待指定drain再发D；96条预取全部零L1 |

约p90实际为14720／16384＝89.84375%，不是精确90%或运行时命中率。严格1000输出档未运行，不填0，不与自然协议混合。严格HTTP检查和等待包含在客户端TTFT中，不直接减去一个屏障均值当后端净耗时。

## 三、验证结果

<a id="client-summary"></a>

### 3.1 客户端性能对比：严格读池整体结果

**本节回答“用户实际等待多久、服务输出多快”。** TTFT从客户端发起请求到首个输出；TPOT是首输出之后每token平均耗时；端到端时延覆盖整个请求。因此这些指标包含入口、P／D交接、KV恢复与推理等实际等待，不只测L2。

将严格三档合成一个固定用例集合：每后端24请求，全部128输出、C1。TTFT／TPOT／端到端时延按完成请求数加权各档均值；吞吐用总token或总请求数÷各档计时总时长，**不平均各档吞吐，也不平均提升百分比**。时长不含seed、启动与档间空隙。input吞吐包含复用token，不代表NPU实际计算吞吐。这是本次集合的描述性汇总，不是新跑的一轮混合负载或容量上限。

[查看严格三档详细表（附录A）](#appendix-strict)

<table class="comparison"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">严格读池三档合计（每后端24请求）</th></tr><tr><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">平均TTFT（ms）</th><td>1,771.275</td><td>946.875</td><td>下降46.54%（改善）</td></tr><tr><th scope="row">平均TPOT（ms/token）</th><td>51.477</td><td>51.137</td><td>下降0.66%（改善）</td></tr><tr><th scope="row">平均端到端时延（ms）</th><td>8,308.857</td><td>7,441.265</td><td>下降10.44%（改善）</td></tr><tr><th scope="row">输入吞吐（token/s）</th><td>1,478.810</td><td>1,651.222</td><td>增加11.66%（改善）</td></tr><tr><th scope="row">输出吞吐（token/s）</th><td>15.404</td><td>17.200</td><td>增加11.66%（改善）</td></tr><tr><th scope="row">请求吞吐（req/s）</th><td>0.120</td><td>0.134</td><td>增加11.66%（改善）</td></tr><tr><th scope="row">整轮时长（s）</th><td>199.425</td><td>178.602</td><td>下降10.44%（改善）</td></tr><tr><th scope="row">完成／失败</th><td>24／0</td><td>24／0</td><td>相同</td></tr><tr><th scope="row">总输入／输出token</th><td>294,912／3,072</td><td>294,912／3,072</td><td>相同</td></tr></tbody></table>

<a id="prefetch-summary"></a>

### 3.2 服务端预取：提前把KV准备到CPU L1

**预取就是：service提前把后面要用的KV从L2池读到CPU L1，准备好再供worker恢复到NPU。** 在本轮严格零L1命中场景，数据先经过 `L2池 → CPU L1`，随后才是 `CPU L1 → NPU KV cache → 推理`。“提前”描述调用次序，不保证已与计算充分重叠。

3.1是用户端整条请求的表现，**本节是内部准备KV的一个阶段，用来解释差异**。本表来自service原生`Prefetch request completed`；计时从controller提交返回后的handle时间起算，含之后的排队、后端执行与完成检查，既不是纯copy时间，也不包含完整H2D和推理，不能与TTFT直接相加或相减。

严格三档每后端共P预取24次、D预取24次；下表耗时按对应预取次数加权，对象数为三档日志确认的L2来源对象数合计，单对象18MiB，不代替SDK实际传输bytes。

[查看各档P／D耗时与对象数（附录B）](#appendix-prefetch)

<table class="comparison"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">严格读池三档合计（每后端P／D各24次）</th></tr><tr><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">P平均预取耗时（ms）</th><td>282.308</td><td>58.333</td><td>下降79.34%（改善）</td></tr><tr><th scope="row">P L2来源对象总数</th><td>1560</td><td>1560</td><td>相同</td></tr><tr><th scope="row">D平均预取耗时（ms）</th><td>392.729</td><td>85.112</td><td>下降78.33%（改善）</td></tr><tr><th scope="row">D L2来源对象总数</th><td>2304</td><td>2304</td><td>相同</td></tr><tr><th scope="row">预取来源中L1命中对象数</th><td>0</td><td>0</td><td>相同</td></tr></tbody></table>

两组后续H2D共用实现；预取阶段变快不能直接称“NPU恢复总耗时降低同样幅度”，仍需单独测H2D及调度重叠。

<a id="other-summary"></a>

### 3.3 基础三档与自然交接四档

两种协议各汇总一组，与严格读池保持分开，统计公式同3.1。基础三档每后端288请求（含C1／C4）；自然交接四档每后端32请求（含128／1000输出），因此吞吐只是对应固定用例集合的结果，不能跨集合直接当容量比较。自然集合1000输出档计时时长较长，对合并吞吐的影响也较大。

[查看基础三档（附录C）](#appendix-basic) · [查看自然交接四档（附录D）](#appendix-natural)

<table class="comparison"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">基础三档合计（每后端288请求）</th><th colspan="3" scope="colgroup">自然交接四档合计（每后端32请求）</th></tr><tr><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">平均TTFT（ms）</th><td>360.436</td><td>325.285</td><td>下降9.75%（改善）</td><td>1,341.827</td><td>864.818</td><td>下降35.55%（改善）</td></tr><tr><th scope="row">平均TPOT（ms/token）</th><td>52.378</td><td>48.076</td><td>下降8.21%（改善）</td><td>50.155</td><td>48.423</td><td>下降3.45%（改善）</td></tr><tr><th scope="row">平均端到端时延（ms）</th><td>1,984.167</td><td>1,815.655</td><td>下降8.49%（改善）</td><td>19,188.634</td><td>17,975.429</td><td>下降6.32%（改善）</td></tr><tr><th scope="row">输入吞吐（token/s）</th><td>1,798.211</td><td>1,962.197</td><td>增加9.12%（改善）</td><td>693.724</td><td>740.546</td><td>增加6.75%（改善）</td></tr><tr><th scope="row">输出吞吐（token/s）</th><td>22.868</td><td>24.953</td><td>增加9.12%（改善）</td><td>18.031</td><td>19.248</td><td>增加6.75%（改善）</td></tr><tr><th scope="row">请求吞吐（req/s）</th><td>0.715</td><td>0.780</td><td>增加9.12%（改善）</td><td>0.052</td><td>0.056</td><td>增加6.75%（改善）</td></tr><tr><th scope="row">整轮时长（s）</th><td>403.014</td><td>369.333</td><td>下降8.36%（改善）</td><td>614.054</td><td>575.230</td><td>下降6.32%（改善）</td></tr><tr><th scope="row">完成／失败</th><td>288／0</td><td>288／0</td><td>相同</td><td>32／0</td><td>32／0</td><td>相同</td></tr><tr><th scope="row">总输入／输出token</th><td>724,704／9,216</td><td>724,704／9,216</td><td>相同</td><td>425,984／11,072</td><td>425,984／11,072</td><td>相同</td></tr></tbody></table>

自然4K的Store D为混合L1／L2来源，不能当作与MF同载荷纯L2对照。历史完整统计见[原指标表](../2026-10-29_l2_performance_report/metrics/tables.md)。

### 3.4 长尾检查：差异是否由少数极慢请求制造

每档8次TTFT整体分布存在间距，不是一两次秒级停顿抬高Store均值；正式结果没有删样本。

生成阶段仍有波动：自然1000输出Store E2E均值54.04s，TPOT均值52.65ms。并发与生成阶段波动不能全部归为读池耗时。[逐条件原始来源与分布核查](../2026-10-30_l2_wait_distribution.md)。

### 3.5 137真实L2 API机理诊断：与模型性能分表

2026-10-29晚至10-30凌晨，137物理8／10，合成字节对象经实际L1 MemoryObj分配器和两后端，13格三次独立run；每格每run2预热＋5计时，采用M-S／S-M／M-S。主集合390次计时batch load，非模型请求，也不是Qwen KV布局验证。下表每行是一个条件的load平均完成时间（ms），横向按三次独立run分组，分别给出两后端均值及该次变化；不将不同run的极值互相相除。

<table class="comparison"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">run1（MF→Store）</th><th colspan="3" scope="colgroup">run2（Store→MF）</th><th colspan="3" scope="colgroup">run3（MF→Store）</th></tr><tr><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">e01_115x18：load均值（ms）</th><td>249.843</td><td>171.759</td><td>下降31.25%（改善）</td><td>256.932</td><td>187.858</td><td>下降26.88%（改善）</td><td>271.536</td><td>182.998</td><td>下降32.61%（改善）</td></tr><tr><th scope="row">e01_128x18：load均值（ms）</th><td>277.556</td><td>186.810</td><td>下降32.69%（改善）</td><td>284.119</td><td>206.971</td><td>下降27.15%（改善）</td><td>293.362</td><td>201.516</td><td>下降31.31%（改善）</td></tr><tr><th scope="row">e01_16x18：load均值（ms）</th><td>39.002</td><td>28.126</td><td>下降27.88%（改善）</td><td>43.912</td><td>34.000</td><td>下降22.57%（改善）</td><td>47.115</td><td>31.458</td><td>下降33.23%（改善）</td></tr><tr><th scope="row">e01_1x18：load均值（ms）</th><td>3.168</td><td>2.125</td><td>下降32.91%（改善）</td><td>3.325</td><td>2.806</td><td>下降15.61%（改善）</td><td>3.349</td><td>2.765</td><td>下降17.44%（改善）</td></tr><tr><th scope="row">e01_32x18：load均值（ms）</th><td>76.918</td><td>55.036</td><td>下降28.45%（改善）</td><td>81.419</td><td>60.883</td><td>下降25.22%（改善）</td><td>87.228</td><td>58.373</td><td>下降33.08%（改善）</td></tr><tr><th scope="row">e01_64x18：load均值（ms）</th><td>146.225</td><td>101.938</td><td>下降30.29%（改善）</td><td>152.826</td><td>114.544</td><td>下降25.05%（改善）</td><td>164.319</td><td>109.251</td><td>下降33.51%（改善）</td></tr><tr><th scope="row">e02_count32_1：load均值（ms）</th><td>12.714</td><td>9.253</td><td>下降27.22%（改善）</td><td>12.700</td><td>8.834</td><td>下降30.44%（改善）</td><td>13.687</td><td>9.031</td><td>下降34.02%（改善）</td></tr><tr><th scope="row">e02_count32_18：load均值（ms）</th><td>76.356</td><td>55.107</td><td>下降27.83%（改善）</td><td>81.487</td><td>61.751</td><td>下降24.22%（改善）</td><td>87.956</td><td>59.117</td><td>下降32.79%（改善）</td></tr><tr><th scope="row">e02_count32_4：load均值（ms）</th><td>24.043</td><td>17.728</td><td>下降26.27%（改善）</td><td>28.083</td><td>22.723</td><td>下降19.08%（改善）</td><td>29.632</td><td>22.273</td><td>下降24.83%（改善）</td></tr><tr><th scope="row">e02_total144_128：load均值（ms）</th><td>48.145</td><td>37.882</td><td>下降21.32%（改善）</td><td>53.212</td><td>36.523</td><td>下降31.36%（改善）</td><td>53.708</td><td>36.673</td><td>下降31.72%（改善）</td></tr><tr><th scope="row">e02_total144_16：load均值（ms）</th><td>23.575</td><td>15.176</td><td>下降35.63%（改善）</td><td>28.615</td><td>20.597</td><td>下降28.02%（改善）</td><td>28.767</td><td>18.316</td><td>下降36.33%（改善）</td></tr><tr><th scope="row">e02_total144_32：load均值（ms）</th><td>25.104</td><td>19.434</td><td>下降22.59%（改善）</td><td>29.874</td><td>25.455</td><td>下降14.79%（改善）</td><td>31.852</td><td>22.766</td><td>下降28.53%（改善）</td></tr><tr><th scope="row">e02_total144_8：load均值（ms）</th><td>19.835</td><td>14.353</td><td>下降27.64%（改善）</td><td>23.769</td><td>19.237</td><td>下降19.06%（改善）</td><td>24.168</td><td>17.412</td><td>下降27.95%（改善）</td></tr></tbody></table>

小对象实际前后均在独立owner，未把client16MiB段的本地命中混入远端对照。Store三组owner退出-6保留，故仍为生命周期未过的诊断。固定总量增对象、固定对象增字节均会增加耗时；shared／torch还改变结果，不能归因到单一pinning或NUMA。

受控前写后读：64×18MiB读前提交32×18MiB新写，MF直接观察到约49—50ms排队；Store外层等待增加但SDK GET本体相近。说明共用worker可形成队头阻塞，不证明它主导历史模型A/B。

进一步拆分代表记录：

| 位置 | 载荷与记录 | 可见耗时 | 边界 |
| --- | --- | --- | --- |
| MF带trace诊断 | 128×18MiB，186.632ms总load | 提交0.029、队列0.077、128次BM同步调用累计180.512、worker其他5.849、结束至返回0.164ms | 另一个带trace实例，不能硬填入关闭trace的主三轮；worker其他含日志／追踪 |
| Store主配对代表样本 | 同载荷，278.289ms总load | SDK综合调用277.313ms，差约0.976ms在外 | SDK内部对象／地址、调度、传输及同步尚未分段；不是纯TE copy |

主要可见时间已收窄到SDK／同步API区间。不能将收益定性为外围少一次RPC；也没有额外payload memcpy的直接证据。[同载荷分段与来源](../2026-10-30_l2_load_breakdown.json)。

### 3.6 独立BM／classic Mooncake TE搬运辅助对照

2026-10-30，137物理10／12，同机CPU DRAM固定热地址，同步完成API，batch=outstanding=1。两端affinity96—119、分配前MPOL_BIND(4)、64KiB对齐。BM普通页／BM池、TE ACL pinned Host与SHM共享页不同，池物理页未全部匹配。本组classic TE不是Store内部TENT，不用其数字替换Store SDK时间。

常规档每路径两个独立run、20预热、100×3计时。左列列出载荷、方向与有效带宽（GB/s）；横向分别对照BM和TE的三条路径，变化为BM→对应TE，带宽越高越好。

<table class="comparison"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">BM vs TE relay</th><th colspan="3" scope="colgroup">BM vs TE RoCE</th><th colspan="3" scope="colgroup">BM vs TE SHM</th></tr><tr><th scope="col">BM SDMA</th><th scope="col">对应TE路径</th><th scope="col">变化结果</th><th scope="col">BM SDMA</th><th scope="col">对应TE路径</th><th scope="col">变化结果</th><th scope="col">BM SDMA</th><th scope="col">对应TE路径</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">1MiB 写带宽（GB/s）</th><td>4.708</td><td>3.910</td><td>下降16.96%（变差）</td><td>4.708</td><td>8.192</td><td>增加74.00%（改善）</td><td>4.708</td><td>16.331</td><td>增加246.87%（改善）</td></tr><tr><th scope="row">1MiB 读带宽（GB/s）</th><td>4.526</td><td>3.420</td><td>下降24.42%（变差）</td><td>4.526</td><td>8.360</td><td>增加84.72%（改善）</td><td>4.526</td><td>14.772</td><td>增加226.39%（改善）</td></tr><tr><th scope="row">4MiB 写带宽（GB/s）</th><td>3.450</td><td>9.635</td><td>增加179.30%（改善）</td><td>3.450</td><td>16.266</td><td>增加371.53%（改善）</td><td>3.450</td><td>14.109</td><td>增加309.01%（改善）</td></tr><tr><th scope="row">4MiB 读带宽（GB/s）</th><td>3.598</td><td>10.040</td><td>增加179.05%（改善）</td><td>3.598</td><td>16.195</td><td>增加350.12%（改善）</td><td>3.598</td><td>12.829</td><td>增加256.57%（改善）</td></tr><tr><th scope="row">18MiB 写带宽（GB/s）</th><td>12.303</td><td>18.420</td><td>增加49.72%（改善）</td><td>12.303</td><td>21.952</td><td>增加78.43%（改善）</td><td>12.303</td><td>13.174</td><td>增加7.08%（改善）</td></tr><tr><th scope="row">18MiB 读带宽（GB/s）</th><td>12.756</td><td>19.272</td><td>增加51.08%（改善）</td><td>12.756</td><td>21.898</td><td>增加71.67%（改善）</td><td>12.756</td><td>12.688</td><td>下降0.53%（变差）</td></tr><tr><th scope="row">64MiB 写带宽（GB/s）</th><td>19.443</td><td>25.096</td><td>增加29.08%（改善）</td><td>19.443</td><td>23.690</td><td>增加21.85%（改善）</td><td>19.443</td><td>13.658</td><td>下降29.75%（变差）</td></tr><tr><th scope="row">64MiB 读带宽（GB/s）</th><td>18.597</td><td>25.464</td><td>增加36.93%（改善）</td><td>18.597</td><td>23.674</td><td>增加27.30%（改善）</td><td>18.597</td><td>13.911</td><td>下降25.20%（变差）</td></tr></tbody></table>

小消息和大块单独采样，完整结果见[独立搬运报告](../2026-10-30_137_te_gap/report.md)：64KiB读取均值BM134.631µs／relay202.318µs／RoCE84.180µs；256MiB读取BM24.453／relay29.924／RoCE24.294GB/s。排序随载荷和方向变化，不能给出普遍BM优势。

实际线程池1→4→1已由原生日志及线程快照确认，在单在途负载下没有观察到改善，不等于并发扩展无收益。主低日志采样103200次计时copy；INFO路线探针24000次另列；全32run双worker退出0，不能替代Store SDK／模型链路的生命周期验收。

### 3.7 验收状态及尚未解决项

| 项目 | 状态与解释 |
| --- | --- |
| 请求／读池来源 | 688计时请求0失败；严格96条预取全部零L1；与完整退出分开验收 |
| 输出文本 | 客户裁剪56／56相等；基础286／288相等；Store ` READY.\n` 与MF ` READY\n`两处原因未定，不归为已定位TE传错KV |
| 生命周期 | 历史P／D SIGABRT、Store service SIGSEGV、owner SIGABRT保留；新worker关闭修复有独立范围，不回写历史 |
| 机制归因 | Store严格轮比自然轮预取慢约1.52—1.67倍，MF基本不变；新进程／池／顺序同时变化，原因尚未闭环 |
| 并发／直传性能 | 多在途、真实batch、同时双向、匹配NPU端点性能未完成；独立native batch功能成功不等于生产集成或TE对照 |
| 最新跨机功能 | 另一队列已验证DEVICE/HBM选池的模型TP1/2/4；每rank仍声明DRAM＋HBM。HOST/DRAM跨机仍失败；真实P/D分置及TP8主要为合成context，不能写成模型跨机TP8 |
| 运行及释放 | M8服务对照20:05释放；137 API队列00:05释放；独立BM/TE00:47:59释放；后续两机功能队列05:29前释放。仅为各自记录时点，非当前空闲证明 |

最新跨机功能不新增本文任何性能结论；其代码已与本文兼容基线不同，实际runtime和归档HEAD参见[配套索引](../2026-10-30_cross_host_execution/code_archive_index.md)。正常退出、输出一致性、多轮对照、SDK内部及物理NUMA仍需分别补齐；目前不宣称跨机HCCS速度收益。

## 四、与客户原始脚本的关系

本次采用客户长上下文材料的结构裁剪，没有直接运行客户GLM原始五档，也未部署DeepSeek-V4。客户原始50%／90%／99%定义尚未查实；本项目候选才明确为公共前缀比例，不能反称客户原件。

| 项目 | 客户原始／参考材料 | 本次两组共同设置 |
| --- | --- | --- |
| 模型 | GLM部署及长上下文说明 | 本地Qwen3-8B，TP1／1P1D |
| 输入／输出 | 128000／190000输入的50%／90%档、198000输入的99%档，输出1000 | 裁剪为4096／16384输入、128／1000输出；基础另有1151／3199输入、32输出 |
| 比例解释 | 原始百分比定义未核实 | 构造公共前缀；50%与89.84375%，不是实际命中率 |
| 数据 | 客户目录与候选长JSONL另有归档 | 本轮合成且经本模型tokenizer验证的长度数据；不使用原128K等大份数据 |
| 服务容量 | 现场归档旧上限16384，不能直接接原五档 | Qwen原配置上下文40960，主性能服务32768；不改公共模型配置 |
| 压测方式 | 参考官方vllm bench serve | 同一CLI，native请求数／输出／并发如第二节；三种协议分表 |
| 对照变量 | 客户参考资料不是本轮对照 | 两组均保留LMCache，只改变L2接入及各后端必要池进程 |

用户提供的PP2终版报告仅为文档格式参考；其中GLM量化、PP2、389客户请求、四D和OFF／ON指标均不是本报告的实验条件。

## 五、实际验证配置与原生命令

主严格三档的两组完整环境前提、池／service、P／D、proxy与seed／benchmark命令分别写全；每个常驻角色使用独立前台终端。以下是历史原命令，不包含自动后台、清退、轮询和重启包装；**历史窗口已结束，文档生成没有执行它们**。结果目录按当时记录保留，将来重测必须换新目录，不能覆盖旧结果。

模型实际argv为`/workspace/d00896497/venv/bin/python -m vllm.entrypoints.cli.main serve ...`，对应同一vLLM serve CLI入口；为保真不悄悄改成另一个环境的`vllm`可执行文件。service保留`service_device_entry.py 2`，它是物理12绑定入口，不可直接删掉。以下各角色的设备掩码和显式环境来自receipt。

**环境缺口：** 本轮env.sh只source旧`l2_ab_20261028/env.sh`，最终包没有完整捕获被source的旧文件；owner／master receipt也未保存完整explicit_env。因此这里是原始启动记录，不能称新机器已验通的一键复建。工作目录未记录处不伪造`cd`；历史环境参考与已知启动器分支见[环境来源说明](../2026-10-29_l2_performance_report/startup_notes.md)。

### 5.1 Mooncake Store L2：完整环境、池端、P／D、代理及benchmark

运行位置：M8个人容器`memfabric-d00896497-478-l2ab-20261028`。每个角色先在对应终端source历史环境，再执行该角色完整命令。

顺序：master及metadata先就绪，再启动owner；owner可用后启动service，随后P／D和proxy。owner／master的全部继承环境未记录，不能仅凭下列argv断言新环境复现成功。

#### Store master

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
/usr/local/python3.12.13/lib/python3.12/site-packages/mooncake/mooncake_master \
  --rpc_port=28501 \
  --rpc_address=127.0.0.1 \
  --rpc_thread_num=2 \
  --memory_allocator=offset \
  --enable_http_metadata_server=true \
  --http_metadata_server_host=127.0.0.1 \
  --http_metadata_server_port=28502 \
  --metrics_port=28509
```

来源：`customer_owner02/processes.json`；[完整历史profile](启动配置/customer02_strict_store.sh)。

#### Store独立owner／物理14

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  ASCEND_RT_VISIBLE_DEVICES=14 \
  /workspace/d00896497/venv/bin/python -u /workspace/d00896497/l2_retest_20261029/customer_runtime/store_owner.py \
  --allow-npu \
  --device 0 \
  --segment-bytes 68719476736 \
  --local-buffer-bytes 67108864 \
  --receipt /workspace/d00896497/l2_retest_20261029/customer_owner02/owner.json \
  --query-dir /workspace/d00896497/l2_retest_20261029/customer_owner02/owner_queries
```

来源：`customer_owner02/processes.json`；[完整历史profile](启动配置/customer02_strict_store.sh)。

#### LMCache service／transport物理12

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  TORCH_DEVICE_BACKEND_AUTOLOAD=1 \
  ASCEND_LOCAL_COMM_RES=1.2 \
  ASCEND_BUFFER_POOL=4:8 \
  LD_LIBRARY_PATH=/usr/local/python3.12.13/lib/python3.12/site-packages/mooncake:/workspace/d00896497/l2_ab_20261028/src/LMCache-Ascend/lmcache_ascend:/workspace/d00896497/driver_copy/lib64/driver:/workspace/d00896497/driver_copy/lib64:/workspace/d00896497/dcmi_copy:/workspace/d00896497/compat/LMCache-Ascend/lmcache_ascend:/usr/local/Ascend/cann-9.1.0/lib64:/usr/local/Ascend/cann-9.1.0/lib64/plugin/opskernel:/usr/local/Ascend/cann-9.1.0/lib64/plugin/nnengine:/usr/local/Ascend/cann-9.1.0/opp/built-in/op_impl/ai_core/tbe/op_tiling/lib/linux/aarch64:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/driver/lib64:/usr/local/Ascend/driver/lib64/common:/usr/local/Ascend/driver/lib64/driver:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/lib:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/examples:/usr/local/Ascend/nnal/atb/latest/atb/cxx_abi_1/tests/atbopstest:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64:/usr/local/Ascend/ascend-toolkit/latest/tools/aml/lib64/plugin:/usr/local/Ascend/ascend-toolkit/latest/lib64:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/opskernel:/usr/local/Ascend/ascend-toolkit/latest/lib64/plugin/nnengine:/usr/local/Ascend/ascend-toolkit/latest/opp/built-in/op_impl/ai_core/tbe/op_tiling:/usr/local/python3.12.13/lib: \
  ASCEND_RT_VISIBLE_DEVICES=8,10,12,14 \
  OMP_NUM_THREADS=1 \
  TOKENIZERS_PARALLELISM=false \
  /workspace/d00896497/venv/bin/python -u /workspace/d00896497/l2_retest_20261029/service_device_entry.py 2 server \
  --host 127.0.0.1 \
  --port 25585 \
  --http-host 127.0.0.1 \
  --http-port 28185 \
  --chunk-size 128 \
  --l1-size-gb 8 \
  --eviction-policy LRU \
  --supported-transfer-mode lmcache_driven \
  --max-gpu-workers 2 \
  --prometheus-port 29185 \
  --l2-store-policy skip_l1 \
  --l2-prefetch-policy default \
  --l2-adapter '{"type": "mooncake_store", "num_workers": 1, "local_hostname": "127.0.0.1:28507", "metadata_server": "http://127.0.0.1:28502/metadata", "master_server_addr": "127.0.0.1:28501", "global_segment_size": "16777216", "local_buffer_size": "268435456", "protocol": "tcp"}'
```

来源：`runs/customer_store02/processes.json`；[完整历史profile](启动配置/customer02_strict_store.sh)。

#### P模型／物理8

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  LMCACHE_REQUEST_TELEMETRY_TYPE=fastapi \
  LMCACHE_REQUEST_TELEMETRY_ENDPOINT=http://127.0.0.1:25788/api/v1/telemetry \
  ASCEND_RT_VISIBLE_DEVICES=8 \
  OMP_NUM_THREADS=1 \
  TOKENIZERS_PARALLELISM=false \
  /workspace/d00896497/venv/bin/python -m vllm.entrypoints.cli.main serve /workspace/d00896497/models/Qwen3-8B \
  --served-model-name mf-l2-ab \
  --tensor-parallel-size 1 \
  --seed 1024 \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --max-num-batched-tokens 1024 \
  --max-num-seqs 4 \
  --gpu-memory-utilization 0.7 \
  --block-size 128 \
  --enforce-eager \
  --no-enable-prefix-caching \
  --host 127.0.0.1 \
  --kv-transfer-config '{"kv_connector": "LMCacheMPConnector", "kv_role": "kv_both", "kv_connector_extra_config": {"lmcache.mp.host": "tcp://127.0.0.1", "lmcache.mp.port": 25585, "lmcache.mp.mp_transfer_mode": "lmcache_driven"}}' \
  --port 28301
```

来源：`runs/customer_store02/processes.json`；[完整历史profile](启动配置/customer02_strict_store.sh)。

#### D模型／物理10

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  ASCEND_RT_VISIBLE_DEVICES=10 \
  OMP_NUM_THREADS=1 \
  TOKENIZERS_PARALLELISM=false \
  /workspace/d00896497/venv/bin/python -m vllm.entrypoints.cli.main serve /workspace/d00896497/models/Qwen3-8B \
  --served-model-name mf-l2-ab \
  --tensor-parallel-size 1 \
  --seed 1024 \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --max-num-batched-tokens 1024 \
  --max-num-seqs 4 \
  --gpu-memory-utilization 0.7 \
  --block-size 128 \
  --enforce-eager \
  --no-enable-prefix-caching \
  --host 127.0.0.1 \
  --kv-transfer-config '{"kv_connector": "LMCacheMPConnector", "kv_role": "kv_both", "kv_connector_extra_config": {"lmcache.mp.host": "tcp://127.0.0.1", "lmcache.mp.port": 25585, "lmcache.mp.mp_transfer_mode": "lmcache_driven"}}' \
  --port 28302
```

来源：`runs/customer_store02/processes.json`；[完整历史profile](启动配置/customer02_strict_store.sh)。

#### 严格proxy／物理12

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  ASCEND_RT_VISIBLE_DEVICES=12 \
  OMP_NUM_THREADS=1 \
  TOKENIZERS_PARALLELISM=false \
  /workspace/d00896497/venv/bin/python -u /workspace/d00896497/l2_retest_20261029/proxy_pool_barrier.py \
  --host 127.0.0.1 \
  --port 28300 \
  --prefiller-host 127.0.0.1 \
  --prefiller-port 28301 \
  --decoder-host 127.0.0.1 \
  --decoder-port 28302 \
  --telemetry-port 25788
```

来源：`runs/customer_store02/processes.json`；[完整历史profile](启动配置/customer02_strict_store.sh)。

#### 正式轮前的池与缓存初态

各后端为严格协议启动独立的新service／池，随后在同一组进程中顺序执行三档；不是每档重启。每档先发2条seed（输出1），再发8条正式请求；没有手工清L1。skip_l1与严格proxy完成指定drain检查，正式后核对P／D逐次来源为0 L1。下列三对命令依次为seed与timed；seed不计入性能，数据生成和长度校验已完成，本报告不重新生成。

#### 4096/2048/128/C1：seed／正式benchmark

seed（2条，不计性能）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in4096_p50_out128/seed.jsonl \
  --custom-output-len 1 \
  --num-prompts 2 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_store/in4096_p50_out128/seed \
  --result-filename benchmark.json
```

正式（8条）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in4096_p50_out128/timed.jsonl \
  --custom-output-len 128 \
  --num-prompts 8 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_store/in4096_p50_out128/timed \
  --result-filename benchmark.json
```

原件：[seed](压测命令/seed_in4096_p50_out128_store.sh.txt)／[正式命令](压测命令/customer02_strict_in4096_p50_out128_store_r1.sh.txt)／[原生结果](结果原件/customer02_strict_in4096_p50_out128_store_r1.benchmark.json)。

#### 16384/8192/128/C1：seed／正式benchmark

seed（2条，不计性能）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in16384_p50_out128/seed.jsonl \
  --custom-output-len 1 \
  --num-prompts 2 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_store/in16384_p50_out128/seed \
  --result-filename benchmark.json
```

正式（8条）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in16384_p50_out128/timed.jsonl \
  --custom-output-len 128 \
  --num-prompts 8 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_store/in16384_p50_out128/timed \
  --result-filename benchmark.json
```

原件：[seed](压测命令/seed_in16384_p50_out128_store.sh.txt)／[正式命令](压测命令/customer02_strict_in16384_p50_out128_store_r1.sh.txt)／[原生结果](结果原件/customer02_strict_in16384_p50_out128_store_r1.benchmark.json)。

#### 16384/14720/128/C1：seed／正式benchmark

seed（2条，不计性能）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in16384_p90_out128/seed.jsonl \
  --custom-output-len 1 \
  --num-prompts 2 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_store/in16384_p90_out128/seed \
  --result-filename benchmark.json
```

正式（8条）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in16384_p90_out128/timed.jsonl \
  --custom-output-len 128 \
  --num-prompts 8 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_store/in16384_p90_out128/timed \
  --result-filename benchmark.json
```

原件：[seed](压测命令/seed_in16384_p90_out128_store.sh.txt)／[正式命令](压测命令/customer02_strict_in16384_p90_out128_store_r1.sh.txt)／[原生结果](结果原件/customer02_strict_in16384_p90_out128_store_r1.benchmark.json)。

### 5.2 MemFabric L2：完整环境、池端、P／D、代理及benchmark

运行位置：M8个人容器`memfabric-d00896497-478-l2ab-20261028`。每个角色先在对应终端source历史环境，再执行该角色完整命令。

顺序：service/rank0与member/rank1须在各自终端配合加入，不要先等service完全就绪才启动peer；再启动P／D与proxy。

#### LMCache service／transport物理12

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  TORCH_DEVICE_BACKEND_AUTOLOAD=1 \
  ASCEND_RT_VISIBLE_DEVICES=8,10,12,14 \
  OMP_NUM_THREADS=1 \
  TOKENIZERS_PARALLELISM=false \
  /workspace/d00896497/venv/bin/python -u /workspace/d00896497/l2_retest_20261029/service_device_entry.py 2 server \
  --host 127.0.0.1 \
  --port 25585 \
  --http-host 127.0.0.1 \
  --http-port 28185 \
  --chunk-size 128 \
  --l1-size-gb 8 \
  --eviction-policy LRU \
  --supported-transfer-mode lmcache_driven \
  --max-gpu-workers 2 \
  --prometheus-port 29185 \
  --l2-store-policy skip_l1 \
  --l2-prefetch-policy default \
  --l2-adapter '{"type": "plugin", "module_path": "lmcache_ascend.v1.distributed.memfabric_l2_adapter", "class_name": "MemFabricL2Adapter", "adapter_params": {"device_id": 2, "world_size": 2, "pool_gb": 64, "storage_rank": 1, "slot_size_mb": 32, "store_url": "tcp://127.0.0.1:28685"}}'
```

来源：`runs/customer_mf02/processes.json`；[完整历史profile](启动配置/customer02_strict_memfabric.sh)。

#### BM pool member／物理14

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  ASCEND_RT_VISIBLE_DEVICES=14 \
  OMP_NUM_THREADS=1 \
  TOKENIZERS_PARALLELISM=false \
  /workspace/d00896497/venv/bin/python -u /workspace/d00896497/l2_retest_20261029/src/LMCache-Ascend/examples/memfabric/pool_member.py \
  --device 0 \
  --rank 1 \
  --world-size 2 \
  --pool-gb 64 \
  --store-url tcp://127.0.0.1:28685
```

来源：`runs/customer_mf02/processes.json`；[完整历史profile](启动配置/customer02_strict_memfabric.sh)。

#### P模型／物理8

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  LMCACHE_REQUEST_TELEMETRY_TYPE=fastapi \
  LMCACHE_REQUEST_TELEMETRY_ENDPOINT=http://127.0.0.1:25788/api/v1/telemetry \
  ASCEND_RT_VISIBLE_DEVICES=8 \
  OMP_NUM_THREADS=1 \
  TOKENIZERS_PARALLELISM=false \
  /workspace/d00896497/venv/bin/python -m vllm.entrypoints.cli.main serve /workspace/d00896497/models/Qwen3-8B \
  --served-model-name mf-l2-ab \
  --tensor-parallel-size 1 \
  --seed 1024 \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --max-num-batched-tokens 1024 \
  --max-num-seqs 4 \
  --gpu-memory-utilization 0.7 \
  --block-size 128 \
  --enforce-eager \
  --no-enable-prefix-caching \
  --host 127.0.0.1 \
  --kv-transfer-config '{"kv_connector": "LMCacheMPConnector", "kv_role": "kv_both", "kv_connector_extra_config": {"lmcache.mp.host": "tcp://127.0.0.1", "lmcache.mp.port": 25585, "lmcache.mp.mp_transfer_mode": "lmcache_driven"}}' \
  --port 28301
```

来源：`runs/customer_mf02/processes.json`；[完整历史profile](启动配置/customer02_strict_memfabric.sh)。

#### D模型／物理10

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  ASCEND_RT_VISIBLE_DEVICES=10 \
  OMP_NUM_THREADS=1 \
  TOKENIZERS_PARALLELISM=false \
  /workspace/d00896497/venv/bin/python -m vllm.entrypoints.cli.main serve /workspace/d00896497/models/Qwen3-8B \
  --served-model-name mf-l2-ab \
  --tensor-parallel-size 1 \
  --seed 1024 \
  --dtype bfloat16 \
  --max-model-len 32768 \
  --max-num-batched-tokens 1024 \
  --max-num-seqs 4 \
  --gpu-memory-utilization 0.7 \
  --block-size 128 \
  --enforce-eager \
  --no-enable-prefix-caching \
  --host 127.0.0.1 \
  --kv-transfer-config '{"kv_connector": "LMCacheMPConnector", "kv_role": "kv_both", "kv_connector_extra_config": {"lmcache.mp.host": "tcp://127.0.0.1", "lmcache.mp.port": 25585, "lmcache.mp.mp_transfer_mode": "lmcache_driven"}}' \
  --port 28302
```

来源：`runs/customer_mf02/processes.json`；[完整历史profile](启动配置/customer02_strict_memfabric.sh)。

#### 严格proxy／物理12

运行前环境（同一终端）：

```bash
source /workspace/d00896497/l2_retest_20261029/env.sh
```

```bash
env \
  ASCEND_RT_VISIBLE_DEVICES=12 \
  OMP_NUM_THREADS=1 \
  TOKENIZERS_PARALLELISM=false \
  /workspace/d00896497/venv/bin/python -u /workspace/d00896497/l2_retest_20261029/proxy_pool_barrier.py \
  --host 127.0.0.1 \
  --port 28300 \
  --prefiller-host 127.0.0.1 \
  --prefiller-port 28301 \
  --decoder-host 127.0.0.1 \
  --decoder-port 28302 \
  --telemetry-port 25788
```

来源：`runs/customer_mf02/processes.json`；[完整历史profile](启动配置/customer02_strict_memfabric.sh)。

#### 正式轮前的池与缓存初态

各后端为严格协议启动独立的新service／池，随后在同一组进程中顺序执行三档；不是每档重启。每档先发2条seed（输出1），再发8条正式请求；没有手工清L1。skip_l1与严格proxy完成指定drain检查，正式后核对P／D逐次来源为0 L1。下列三对命令依次为seed与timed；seed不计入性能，数据生成和长度校验已完成，本报告不重新生成。

#### 4096/2048/128/C1：seed／正式benchmark

seed（2条，不计性能）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in4096_p50_out128/seed.jsonl \
  --custom-output-len 1 \
  --num-prompts 2 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_memfabric/in4096_p50_out128/seed \
  --result-filename benchmark.json
```

正式（8条）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in4096_p50_out128/timed.jsonl \
  --custom-output-len 128 \
  --num-prompts 8 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_memfabric/in4096_p50_out128/timed \
  --result-filename benchmark.json
```

原件：[seed](压测命令/seed_in4096_p50_out128_memfabric.sh.txt)／[正式命令](压测命令/customer02_strict_in4096_p50_out128_memfabric_r1.sh.txt)／[原生结果](结果原件/customer02_strict_in4096_p50_out128_memfabric_r1.benchmark.json)。

#### 16384/8192/128/C1：seed／正式benchmark

seed（2条，不计性能）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in16384_p50_out128/seed.jsonl \
  --custom-output-len 1 \
  --num-prompts 2 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_memfabric/in16384_p50_out128/seed \
  --result-filename benchmark.json
```

正式（8条）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in16384_p50_out128/timed.jsonl \
  --custom-output-len 128 \
  --num-prompts 8 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_memfabric/in16384_p50_out128/timed \
  --result-filename benchmark.json
```

原件：[seed](压测命令/seed_in16384_p50_out128_memfabric.sh.txt)／[正式命令](压测命令/customer02_strict_in16384_p50_out128_memfabric_r1.sh.txt)／[原生结果](结果原件/customer02_strict_in16384_p50_out128_memfabric_r1.benchmark.json)。

#### 16384/14720/128/C1：seed／正式benchmark

seed（2条，不计性能）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in16384_p90_out128/seed.jsonl \
  --custom-output-len 1 \
  --num-prompts 2 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_memfabric/in16384_p90_out128/seed \
  --result-filename benchmark.json
```

正式（8条）：

```bash
/workspace/d00896497/venv/bin/vllm bench serve \
  --backend openai \
  --base-url http://127.0.0.1:28300 \
  --endpoint /v1/completions \
  --model mf-l2-ab \
  --served-model-name mf-l2-ab \
  --tokenizer /workspace/d00896497/models/Qwen3-8B \
  --dataset-name custom \
  --dataset-path /workspace/d00896497/l2_retest_20261029/customer_data/in16384_p90_out128/timed.jsonl \
  --custom-output-len 128 \
  --num-prompts 8 \
  --max-concurrency 1 \
  --request-rate inf \
  --temperature 0 \
  --num-warmups 0 \
  --ready-check-timeout-sec 0 \
  --ignore-eos \
  --disable-shuffle \
  --no-oversample \
  --seed 0 \
  --skip-chat-template \
  --percentile-metrics ttft,tpot,e2el \
  --metric-percentiles 50,95 \
  --save-result \
  --save-detailed \
  --result-dir /workspace/d00896497/l2_retest_20261029/customer_results/customer02_strict_memfabric/in16384_p90_out128/timed \
  --result-filename benchmark.json
```

原件：[seed](压测命令/seed_in16384_p90_out128_memfabric.sh.txt)／[正式命令](压测命令/customer02_strict_in16384_p90_out128_memfabric_r1.sh.txt)／[原生结果](结果原件/customer02_strict_in16384_p90_out128_memfabric_r1.benchmark.json)。

### 5.3 基础／自然协议及差异入口

两种后端P／D对应argv及explicit_env一致，service的`--l2-adapter`与池端是后端选择点。严格两组proxy均使用同一个`proxy_pool_barrier.py`，不会只给一组增加等待。

| 历史profile | 完整记录 | 与主命令的关系 |
| --- | --- | --- |
| 基础Store | [basic_store.sh](启动配置/basic_store.sh) | max-model-len4096、L1=4GiB、池4GiB；自然proxy；客户端三条件／三轮 |
| 基础MF | [basic_memfabric.sh](启动配置/basic_memfabric.sh) | 同基础容量；后端选择及池端不同 |
| 自然Store | [customer01_store.sh](启动配置/customer01_store.sh) | 主容量32768／8GiB／64GiB；原兼容proxy，无P后drain shim |
| 自然MF | [customer01_memfabric.sh](启动配置/customer01_memfabric.sh) | 同自然容量与proxy；后端选择及池端不同 |

这些profile的全部角色均在附件中写全，启动行默认全部注释，直接bash不会启动服务；本节不是声称它们与严格组命令完全相同。32份计时命令在[命令清单](压测命令/manifest.json)，基础warmup与seed经历见第二节，不将额外warmup混入正式计数。

独立137裸搬运原命令来自其`processes.json`，与本节模型服务完全不同。原始冻结入口与参数保存在[独立原始包](../2026-10-30_137_te_gap/raw_evidence.tar.gz)，统计与路线见[该组报告](../2026-10-30_137_te_gap/index.html)；本报告不将它改写成Store内部可执行的TE替代实现。

## 六、结果与配置原件

| 材料 | 归档入口 |
| --- | --- |
| 本报告结构化指标／来源 | [report_metrics.json](report_metrics.json)，保留协议、主版本、原路径及API三轮均值 |
| 32份原生服务benchmark | [结果原件](结果原件/)，从原归档逐字节复制，没有重跑或改值 |
| 两组完整历史启动profile | [启动配置](启动配置/)；主三档在第五节展开，其他四份在附件 |
| 32份计时与6份seed命令 | [压测命令](压测命令/)；[清单](压测命令/manifest.json)保留对应结果及来源 |
| 原服务归档与实际路径 | [原完整报告](../2026-10-29_l2_performance_report/index.html)、[计量口径](../2026-10-29_l2_performance_report/metrics_notes.md)、[原始服务包](../2026-10-29_478_l2_retest/collected_final.tar.gz) |
| L2读池来源／字节／输出与退出 | [实际执行与来源报告](../2026-10-29_478_l2_retest/本轮结果与L2证据.md)、[严格预取审计](../2026-10-29_478_l2_retest/strict_prefetch_audit.json) |
| 137真实L2 API与机理原件 | [验证报告](../2026-10-29_137_l2_mechanism/验证报告.md)、[原始包](../2026-10-29_137_l2_mechanism/final_raw_evidence.tar.gz) |
| 代表load细分与逐请求分布 | [load分段](../2026-10-30_l2_load_breakdown.json)、[分布核查](../2026-10-30_l2_wait_distribution.md) |
| 独立BM／TE数据与真实路线 | [搬运报告](../2026-10-30_137_te_gap/index.html)、[quiet统计](../2026-10-30_137_te_gap/quiet_summary.json)、[路线证据](../2026-10-30_137_te_gap/route_notes.md) |
| 后续跨机／多卡功能，不是本文性能 | [最新功能汇总](../2026-10-30_cross_host_execution/validation_report.md)、[实际代码与文档HEAD区分](../2026-10-30_cross_host_execution/code_archive_index.md) |
| 本地生成入口 | [生成测试对比报告.py](生成测试对比报告.py)，只读归档与生成文档，无服务／SSH调用 |
| 本地文档核验 | [report_checks.json](report_checks.json)，范围仅指标／命令提取／链接与编码 |

原始JSON、脚本、失败和慢样本保持不变；本报告的范围选择不等于删除历史记录。本文未推送到外部仓库，内部IP、路径和原始资料按个人项目本地保留。文档准备完成不等于新一轮实验已安排。
## 附录：分档结果与正文汇总口径

正文按同一协议的固定用例集合汇总，本附录保留每一档的原结果与负向变化。所有变化均为Store→MemFabric；平均耗时越低越好，吞吐越高越好。分档表头为“输入／公共前缀／输出／并发”，宽表可横向滚动。

<a id="appendix-strict"></a>

### 附录A：严格读池三档客户端详情

[返回3.1整体结果](#client-summary)

<table class="comparison"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">4096/2048/128/C1</th><th colspan="3" scope="colgroup">16384/8192/128/C1</th><th colspan="3" scope="colgroup">16384/14720/128/C1</th></tr><tr><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">平均TTFT（ms）</th><td>749.706</td><td>459.122</td><td>下降38.76%（改善）</td><td>2,494.146</td><td>1,528.501</td><td>下降38.72%（改善）</td><td>2,069.973</td><td>853.002</td><td>下降58.79%（改善）</td></tr><tr><th scope="row">平均TPOT（ms/token）</th><td>53.195</td><td>50.629</td><td>下降4.82%（改善）</td><td>50.909</td><td>51.351</td><td>增加0.87%（变差）</td><td>50.327</td><td>51.431</td><td>增加2.19%（变差）</td></tr><tr><th scope="row">平均端到端时延（ms）</th><td>7,505.475</td><td>6,889.001</td><td>下降8.21%（改善）</td><td>8,959.609</td><td>8,050.103</td><td>下降10.15%（改善）</td><td>8,461.486</td><td>7,384.693</td><td>下降12.73%（改善）</td></tr><tr><th scope="row">输入吞吐（token/s）</th><td>545.698</td><td>594.528</td><td>增加8.95%（改善）</td><td>1,828.544</td><td>2,035.131</td><td>增加11.30%（改善）</td><td>1,936.179</td><td>2,218.492</td><td>增加14.58%（改善）</td></tr><tr><th scope="row">输出吞吐（token/s）</th><td>17.053</td><td>18.579</td><td>增加8.95%（改善）</td><td>14.285</td><td>15.899</td><td>增加11.30%（改善）</td><td>15.126</td><td>17.332</td><td>增加14.58%（改善）</td></tr><tr><th scope="row">请求吞吐（req/s）</th><td>0.133</td><td>0.145</td><td>增加8.95%（改善）</td><td>0.112</td><td>0.124</td><td>增加11.30%（改善）</td><td>0.118</td><td>0.135</td><td>增加14.58%（改善）</td></tr><tr><th scope="row">整轮时长（s）</th><td>60.048</td><td>55.116</td><td>下降8.21%（改善）</td><td>71.681</td><td>64.405</td><td>下降10.15%（改善）</td><td>67.696</td><td>59.082</td><td>下降12.73%（改善）</td></tr><tr><th scope="row">完成／失败</th><td>8／0</td><td>8／0</td><td>相同</td><td>8／0</td><td>8／0</td><td>相同</td><td>8／0</td><td>8／0</td><td>相同</td></tr><tr><th scope="row">总输入／输出token</th><td>32,768／1,024</td><td>32,768／1,024</td><td>相同</td><td>131,072／1,024</td><td>131,072／1,024</td><td>相同</td><td>131,072／1,024</td><td>131,072／1,024</td><td>相同</td></tr></tbody></table>


<a id="appendix-prefetch"></a>

### 附录B：严格三档服务端预取及L2来源

[返回3.2预取概念与汇总](#prefetch-summary)

每档每后端P／D各8次预取。对象数是每次预取的L2来源对象数，计时边界同3.2。

<table class="comparison"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">4096/2048/128/C1</th><th colspan="3" scope="colgroup">16384/8192/128/C1</th><th colspan="3" scope="colgroup">16384/14720/128/C1</th></tr><tr><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">P预取均值（ms）</th><td>78.112</td><td>16.350</td><td>下降79.07%（改善）</td><td>282.562</td><td>57.625</td><td>下降79.61%（改善）</td><td>486.250</td><td>101.025</td><td>下降79.22%（改善）</td></tr><tr><th scope="row">P每请求L2对象数</th><td>16</td><td>16</td><td>相同</td><td>64</td><td>64</td><td>相同</td><td>115</td><td>115</td><td>相同</td></tr><tr><th scope="row">D预取均值（ms）</th><td>143.200</td><td>29.913</td><td>下降79.11%（改善）</td><td>507.225</td><td>112.662</td><td>下降77.79%（改善）</td><td>527.763</td><td>112.763</td><td>下降78.63%（改善）</td></tr><tr><th scope="row">D每请求L2对象数</th><td>32</td><td>32</td><td>相同</td><td>128</td><td>128</td><td>相同</td><td>128</td><td>128</td><td>相同</td></tr></tbody></table>


<a id="appendix-basic"></a>

### 附录C：基础三档客户端详情

[返回3.3整体结果](#other-summary)

每后端每档3轮、合计96条计时请求；原有两处句点差异及退出问题保留。

<table class="comparison"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">1151/1024/32/C1</th><th colspan="3" scope="colgroup">3199/3072/32/C1</th><th colspan="3" scope="colgroup">3199/3072/32/C4</th></tr><tr><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">平均TTFT（ms）</th><td>232.182</td><td>212.396</td><td>下降8.52%（改善）</td><td>304.787</td><td>268.505</td><td>下降11.90%（改善）</td><td>544.337</td><td>494.956</td><td>下降9.07%（改善）</td></tr><tr><th scope="row">平均TPOT（ms/token）</th><td>49.544</td><td>44.943</td><td>下降9.29%（改善）</td><td>49.329</td><td>46.122</td><td>下降6.50%（改善）</td><td>58.262</td><td>53.164</td><td>下降8.75%（改善）</td></tr><tr><th scope="row">平均端到端时延（ms）</th><td>1,768.049</td><td>1,605.637</td><td>下降9.19%（改善）</td><td>1,833.986</td><td>1,698.289</td><td>下降7.40%（改善）</td><td>2,350.467</td><td>2,143.040</td><td>下降8.82%（改善）</td></tr><tr><th scope="row">输入吞吐（token/s）</th><td>650.839</td><td>716.654</td><td>增加10.11%（改善）</td><td>1,743.874</td><td>1,883.174</td><td>增加7.99%（改善）</td><td>5,375.103</td><td>5,897.699</td><td>增加9.72%（改善）</td></tr><tr><th scope="row">输出吞吐（token/s）</th><td>18.095</td><td>19.924</td><td>增加10.11%（改善）</td><td>17.444</td><td>18.838</td><td>增加7.99%（改善）</td><td>53.768</td><td>58.995</td><td>增加9.72%（改善）</td></tr><tr><th scope="row">请求吞吐（req/s）</th><td>0.565</td><td>0.623</td><td>增加10.11%（改善）</td><td>0.545</td><td>0.589</td><td>增加7.99%（改善）</td><td>1.680</td><td>1.844</td><td>增加9.72%（改善）</td></tr><tr><th scope="row">整轮时长（s）</th><td>169.775</td><td>154.183</td><td>下降9.18%（改善）</td><td>176.104</td><td>163.078</td><td>下降7.40%（改善）</td><td>57.135</td><td>52.072</td><td>下降8.86%（改善）</td></tr><tr><th scope="row">完成／失败</th><td>96／0</td><td>96／0</td><td>相同</td><td>96／0</td><td>96／0</td><td>相同</td><td>96／0</td><td>96／0</td><td>相同</td></tr><tr><th scope="row">总输入／输出token</th><td>110,496／3,072</td><td>110,496／3,072</td><td>相同</td><td>307,104／3,072</td><td>307,104／3,072</td><td>相同</td><td>307,104／3,072</td><td>307,104／3,072</td><td>相同</td></tr></tbody></table>


<a id="appendix-natural"></a>

### 附录D：自然交接四档客户端详情

[返回3.3整体结果](#other-summary)

每后端每档8请求。4K Store D为8 L1＋24 L2，MF为32 L2，该档为混合路径，不能作同载荷纯L2结论。1000输出没有对应严格协议结果。

<table class="comparison"><thead><tr><th rowspan="2" scope="col">指标</th><th colspan="3" scope="colgroup">4096/2048/128/C1</th><th colspan="3" scope="colgroup">16384/8192/128/C1</th><th colspan="3" scope="colgroup">16384/14720/128/C1</th><th colspan="3" scope="colgroup">16384/14720/1000/C1</th></tr><tr><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th><th scope="col">Mooncake Store L2</th><th scope="col">MemFabric L2</th><th scope="col">变化结果</th></tr></thead><tbody><tr><th scope="row">平均TTFT（ms）</th><td>544.129</td><td>399.679</td><td>下降26.55%（改善）</td><td>1,956.819</td><td>1,471.546</td><td>下降24.80%（改善）</td><td>1,419.401</td><td>787.969</td><td>下降44.49%（改善）</td><td>1,446.958</td><td>800.079</td><td>下降44.71%（改善）</td></tr><tr><th scope="row">平均TPOT（ms/token）</th><td>49.090</td><td>47.709</td><td>下降2.81%（改善）</td><td>49.676</td><td>48.533</td><td>下降2.30%（改善）</td><td>49.206</td><td>47.172</td><td>下降4.13%（改善）</td><td>52.647</td><td>50.279</td><td>下降4.50%（改善）</td></tr><tr><th scope="row">平均端到端时延（ms）</th><td>6,778.580</td><td>6,458.731</td><td>下降4.72%（改善）</td><td>8,265.642</td><td>7,635.224</td><td>下降7.63%（改善）</td><td>7,668.534</td><td>6,778.811</td><td>下降11.60%（改善）</td><td>54,041.782</td><td>51,028.949</td><td>下降5.58%（改善）</td></tr><tr><th scope="row">输入吞吐（token/s）</th><td>604.206</td><td>634.133</td><td>增加4.95%（改善）</td><td>1,982.045</td><td>2,145.702</td><td>增加8.26%（改善）</td><td>2,136.371</td><td>2,416.766</td><td>增加13.12%（改善）</td><td>303.170</td><td>321.069</td><td>增加5.90%（改善）</td></tr><tr><th scope="row">输出吞吐（token/s）</th><td>18.881</td><td>19.817</td><td>增加4.95%（改善）</td><td>15.485</td><td>16.763</td><td>增加8.26%（改善）</td><td>16.690</td><td>18.881</td><td>增加13.12%（改善）</td><td>18.504</td><td>19.597</td><td>增加5.90%（改善）</td></tr><tr><th scope="row">请求吞吐（req/s）</th><td>0.148</td><td>0.155</td><td>增加4.95%（改善）</td><td>0.121</td><td>0.131</td><td>增加8.26%（改善）</td><td>0.130</td><td>0.148</td><td>增加13.12%（改善）</td><td>0.019</td><td>0.020</td><td>增加5.90%（改善）</td></tr><tr><th scope="row">整轮时长（s）</th><td>54.233</td><td>51.674</td><td>下降4.72%（改善）</td><td>66.130</td><td>61.086</td><td>下降7.63%（改善）</td><td>61.353</td><td>54.234</td><td>下降11.60%（改善）</td><td>432.339</td><td>408.236</td><td>下降5.58%（改善）</td></tr><tr><th scope="row">完成／失败</th><td>8／0</td><td>8／0</td><td>相同</td><td>8／0</td><td>8／0</td><td>相同</td><td>8／0</td><td>8／0</td><td>相同</td><td>8／0</td><td>8／0</td><td>相同</td></tr><tr><th scope="row">总输入／输出token</th><td>32,768／1,024</td><td>32,768／1,024</td><td>相同</td><td>131,072／1,024</td><td>131,072／1,024</td><td>相同</td><td>131,072／1,024</td><td>131,072／1,024</td><td>相同</td><td>131,072／8,000</td><td>131,072／8,000</td><td>相同</td></tr></tbody></table>


</details>
