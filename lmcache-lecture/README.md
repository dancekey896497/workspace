# LMCache 图解讲义

用 HTML + 图示讲解 LMCache 的数据流、组件组成、LMCache-Ascend 的作用，
以及 CUDA/NPU IPC、SHM、Pickle 的运作方式与 IPC 的分层结构。

## 怎么打开

直接双击 `index.html`，或拖进浏览器。**纯静态、零外部依赖、可离线阅读**
（没有 CDN、没有 JS，图全部是内联 SVG，样式只有一份 `style.css`）。

建议从 `index.html` 的目录页进入，按顺序读。

## 章节

| 文件 | 内容 |
|---|---|
| `index.html` | 目录 + 全景图 + 阅读路径建议 |
| `01-basics.html` | KV Cache 是什么、Prefill/Decode、分页与 chunk、**L1/L2/L3 是什么** |
| `02-components.html` | LMCache 组件逐个拆解（CacheEngine / StorageManager / TokenDatabase / Allocator / Backend / Connector / Serializer）+ 仓库目录地图 |
| `03-dataflow.html` | 存/取两条数据流的完整时序、命中判断、**layerwise 时间线**、配置示例 |
| `04-lmcache-ascend.html` | 昇腾适配层：运行期 patch 机制、被替换的组件、昇腾特有的坑 |
| `05-ipc-shm-pickle.html` | 三种机制的分工、完整调用序列、CUDA IPC 六步、SHM 与 fd 传递、常见坑 |
| `06-vllm-connector.html` | vLLM 侧全部 LMCache 相关组件与回调、`kv_role` 含义、引擎钩子时序 |
| `07-npu-ipc-stack.html` | **IPC 从驱动到应用的七层对照**（CUDA vs torch-npu）、DMA 路径、按层排错 |
| `99-reference.html` | 代码索引、API 清单、配置项、术语表、外部链接、动手验证清单 |
| `style.css` | 共享样式 |

## 关于代码片段的说明

本讲义里的代码片段是**结构摘要**，保留真实的类名、方法名与文件路径，但省略了参数细节
（vLLM 的 connector 接口在 0.8 → 0.9 → 0.10 之间改过多次）。
凡标注「版本相关」或「需在机器上核实」的地方，请以你实际安装版本的代码为准。

LMCache-Ascend 章节尤其如此：撰写时网络无法读取该仓库源码，内容基于其 README 与官方文档的公开描述，
具体类名/文件名请用 `pip show -f lmcache-ascend` 核对自己环境。

## 参考来源

- LMCache <https://github.com/LMCache/LMCache>
- LMCache-Ascend <https://github.com/LMCache/LMCache-Ascend>
- vLLM <https://github.com/vllm-project/vllm>
- vLLM-Ascend 的 LMCache-Ascend 部署指南 <https://docs.vllm.ai/projects/ascend/en/main/user_guide/feature_guide/lmcache_ascend_deployment.html>
- 官方博客《LMCache × 昇腾》 <https://blog.lmcache.ai/zh/2025/11/04/>
- 昇腾《内存共享（IPC）》文档 <https://www.hiascend.com/document/detail/zh/Pytorch/720/ptmoddevg/Frameworkfeatures/featuresguide_00031.html>
