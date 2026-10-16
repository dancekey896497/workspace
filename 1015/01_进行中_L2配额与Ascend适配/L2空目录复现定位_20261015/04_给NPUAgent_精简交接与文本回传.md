# 给 NPU 侧 Agent：补正配额实验并回传事实

## 任务结论与分工

**先补一组“IsolatedLRU + 正配额”实验，再回传 NPU 实际运行信息。本地负责源码和 GPU/NPU 差异分析；你这侧只有 NPU，不需要寻找 GPU 环境或运行 GPU 对照。**

已有实验：`fs_native + use_odirect=false`，带 IsolatedLRU 时成功保存 12 个对象、驱逐 12 个、用量归零；去掉 eviction 后保留 12 个文件，合计 `1,811,939,328 bytes = 1.6875 GiB`。本次只验证：保留策略、补正配额后能否保留文件。

## 一、执行一个实验

1. 沿用上次成功的 NPU 环境、Qwen3-8B、TP4、4096 chunk、长请求和实际可用传输模式；保留 IsolatedLRU、水位 0.8、比例 0.1，保持 `use_odirect=false`。不要为本实验升级依赖、改源码、切换 allocator 或应用其他补丁。
2. 使用自己的隔离实例和新 L2 目录，记录服务 PID、RPC/HTTP 端口和 `base_path`。确认 vLLM 指向这个测试实例；先不要发业务请求。
3. 确认请求**实际** `cache_salt`。可沿用此前 `over quota` 日志中的值，或核对请求/IPC key。不能确认时先完成这项确认，不要猜一个 salt 登记。
4. 为实际 salt 登记 **8 GiB**，GET 确认成功后，再发一条与上次长度接近、**开头前缀不同**的长请求，避免全部命中已有 L1。
5. 保存请求前的指标。成功保存后，在约 `0、2、5、10 秒`记录成功对象累计计数、驱逐累计计数、L2 usage、文件数和总字节数；据此计算增量。附相关保存/清理/失败日志原文，不必发送完整日志文件。

下面只适用于确认实际 salt 为 `""` 的情况；HTTP 地址必须替换为自己的实例：

```bash
# 先确认这个 HTTP 端口及 /status 的 base_path 属于本次测试实例。
LM_HTTP='http://127.0.0.1:18080'

curl --fail --silent --show-error \
  -X PUT "$LM_HTTP/quota/_default" \
  -H 'Content-Type: application/json' \
  -d '{"limit_gb":8}'

curl --fail --silent --show-error "$LM_HTTP/quota/_default"
```

预期 `exists=true`、`limit_gb=8.0`。实际 salt 若为 `tenant_a`，用 `/quota/tenant_a`。`_default` **只表示空字符串，不是通配符**。接口不兼容或鉴权失败时记录 HTTP 状态和响应，按本地已有接口处理，不升级环境绕过。

**通过标准：**有新的成功保存对象；约 1.6875 GiB 数据低于 `8 × 0.8 = 6.4 GiB` 水位；文件及用量跨多个清理周期保留，没有对应的新增驱逐。不要强求仍是 12 个文件，以本次实际新增对象为准。

| 结果 | 本次如何处理 |
|---|---|
| 文件保留、无新增驱逐 | 记录通过，结束实验 |
| GET 正确，但日志仍 `limit=0` | 核对实际 salt、HTTP 实例及配额读取位置，回传差异 |
| 无新提交/无成功对象 | 该样本不能判断配额修复；先确认新前缀、实际 token 数及 L2 提交/完成状态 |
| store failed | 保留首个异常；没有底层 errno 就写“未取得”，不要猜 EFAULT |
| 成功对象增加、无驱逐但目录为空 | 回传实际 base_path、目录观察位置和相关日志，先不扩大实验 |
| 正配额下实际用量超过 6.4 GiB | 不按“预算内保留”判定；记录真实用量与清理日志后回传 |

本次不做删除配额、小配额驱逐、O_DIRECT 重测或整套内存探针。不要清生产缓存、使用广泛 `pkill`；仅管理自己创建的进程。结束后回传测试服务是否保留及其路径。

## 二、顺手采集 NPU 事实，供本地比较

**只取实际版本和运行证据，不要求你解释 GPU 为什么正常。**

- LMCache、LMCache-Ascend、vLLM 的 commit/安装版本及实际导入文件路径；原生 FS `.so` 的加载路径。没有 commit 则写明未取得，不用版本名冒充提交号。
- 实际 allocator 类名、实际 transfer mode 及对应日志；不能仅凭“没传 lazy 参数”判断关闭，也不能把 `auto` 当作最终模式。无法从现有信息确认的项目写“未核实”，本次不为它追加侵入式探针。
- 实际 salt、quota GET 原文，以及是否存在其他配额初始化程序。
- 实际加载的源码中三处简短片段：①未知 salt 的 `get_limit_bytes()` 返回值；②`limit == 0` 时的清理比例；③启动时 QuotaManager 的创建/初始化位置。每处附文件路径和行号即可，不发送整份文件。

我们在本地将这些事实与公开 GPU 通用路径核对。**没有 GPU 运行数据时，GPU 配额、实际 salt 和驱逐线程状态仍标“未核实”，不能声称已完成 GPU/NPU 实机对照。**

## 三、直接复制下面的文本回传

```text
【结论先行】
正配额实验：通过 / 失败 / 未完成
依据（1—3句，只写观察到的事实）：

【实例与版本】
测试 PID / RPC端口 / HTTP端口 / L2 base_path：
vLLM 实际连接的测试 RPC 地址：
LMCache commit或安装版本 / 实际导入路径：
LMCache-Ascend commit或安装版本 / 实际导入路径：
vLLM commit或安装版本 / connector文件路径：
native FS .so 加载路径：
实际 allocator / 实际 transfer mode / 证据日志：
PR305修改在本次环境中的状态（已核实/未核实及依据）：

【请求与配额】
模型 / TP / chunk-size / use_odirect：
IsolatedLRU 的 watermark / ratio：
实际 cache_salt（空字符串请明确写 cache_salt=""）/ 确认依据：
PUT 状态码与响应：
GET 配额原文：
实际 prompt_tokens / 新前缀标识：

【观测，计数保留原指标名及标签】
请求前：成功对象计数 / 驱逐计数 / L2 usage bytes / 文件数 / 文件总bytes：
保存完成后约0s：
保存完成后约2s：
保存完成后约5s：
保存完成后约10s：
成功对象增量 / 驱逐增量：
保存、清理或首个失败日志原文（如有，含salt/limit/ratio）：

【供本地分析的源码事实】
未知salt默认配额：路径:行号 + 简短代码
limit=0时清理比例：路径:行号 + 简短代码
QuotaManager初始化：路径:行号 + 简短代码
是否有其他程序自动登记配额：有/无/未核实，依据：

【收尾】
测试服务保留/已停止；日志路径：
未取得或未核实的项目：
GPU环境：本侧没有，未做GPU实机对照。
```
