# 本地候选验证记录

**结论：本地定向测试通过，可作为明日核对与主库评审的候选。真实 Ascend 终版一致性、完整服务验收及严格 DIO 路径仍未核实。**

日期：2026-10-13；基线 `110caf524c728b7b4489b2d4db40e2c72f79a879`；目标 `LMCache/LMCache:dev`。本记录没有将现场总结中的数值当成本机测量。

## 已执行结果

| 检查 | 结果 | 证据/范围 |
|---|---|---|
| 候选回归及相邻既有测试 | **56 passed，1 warning，6.39 s** | `pytest_candidate.log`；新增 staging 9 项，既有 delete/keys 47 项；无跳过 |
| 原始主库基线对照 | **4 failed，4.22 s，符合预期** | `pytest_baseline.log`；只对照下表四项，随后恢复候选文件 |
| mypy 1.17.1 | **通过，2 files** | `mypy.log`；按仓库 pyproject 配置，未跳过导入分析 |
| Ruff 0.11.7 / isort 6.0.1 | **通过** | 两个变更文件的 lint 与导入排序；格式化已完成 |
| codespell 2.4.1 | **通过** | 两个变更文件，仓库配置；模块入口为 `codespell_lib` |
| `git diff --check` | **通过** | 生产 diff 空白检查 |
| 交付 patch 与 SPDX | **通过** | `candidate.patch` 在当前候选工作区反向 `git apply --check` 成功；两个文件通过仓库 SPDX 检查 |
| 独立代码审查 | **无新增阻断性问题** | 一条 store 取消快照覆盖建议已通过第 7 项新增测试补齐；审查没有替代运行测试 |

唯一 warning 来自既有 `test_delete_after_close_does_not_raise`：`FSL2Adapter._execute_delete` coroutine never awaited。触发路径不在此次修改范围。该用例本身通过；本记录不把带 warning 的结果写成零告警，也没有附带修复这一独立问题。

## 新增九项测试的具体范围

本机页大小为 4096 时，对应数值如下；断言来自测试代码，原始测试输出记录各项通过。

| 用例 | 本地参数/判定 | 能证明什么 |
|---|---|---|
| 普通对齐写内存 | 1,028,096 字节；系统调用处断言地址区别于源地址、对齐余数为 0，并校验完整落盘字节 | 实际 write 收到独立对齐可写 view；不模拟 ACL 映射机制 |
| ctypes 目标读回 | 8192 字节、`<B` 目标视图，回读全等 | cast 到字节视图后的复制有效 |
| 短写不发布 | 请求 4096，实际写入/返回 4095；任务失败、统计 0、无 `.data` 或 `.tmp` | 失败不会伪装为成功发布 |
| 旧临时文件截断 | 旧 tmp 20,480 字节，新数据 4096 字节，最终文件精确等于新数据 | 不留下旧文件尾部 |
| 短读不改目标 | 两个对象各 8192，首个文件截为 4096；只第二个命中，首目标保持 `?`，第二个内容完整 | 不完整 payload 不回写；同批后续对象仍继续 |
| read 时关闭 | 阻塞 readinto，close 后把目标改为 `z`，再放行；目标仍全 `z`，worker mmap 已关闭 | 真实取消等待协程后无线程晚写目标 |
| store 时关闭 | worker 在创建 mmap 前阻塞；close 后覆写源为 `z`，放行后写出的仍为原始 `a`；mmap 已关闭、无正式文件 | 快照已在提交线程前取得，关闭后不再读取借用源 |
| buffered 回归 | 16 字节非对齐字符串完整写读 | 原 buffered 路径仍可用 |
| Linux O_DIRECT 回归 | **9,437,184 字节**写读全等；先做独立对齐探针，实际适配器保留 O_DIRECT 标志；此次未跳过 | 当前宿主环境的该参数调用正常；不能证明内核没有 fallback |

除最后一项外，故障注入测试在 `os.open` 边界去掉 O_DIRECT，仍执行真实文件读写，避免挂载差异影响所有权/发布语义测试。关闭用例通过事件明确控制先后，不用固定 sleep 猜测工作线程时序。

## 原始基线对照的失败依据

| 用例 | 原始基线行为 |
|---|---|
| 独立对齐写 | 把借用/未对齐源 view 交到系统调用，被注入检查拒绝，store 失败 |
| 短写 | 实际只写 4095 字节，仍返回成功并报告 4096 |
| 临时文件截断 | 新文件前部正确，但仍含旧尾部 |
| 短读 | 虽未置首个命中位，却已把目标前 4096 字节改为 `a` |

这些故障注入是软件回归证据；没有把它们当作真实设备 EFAULT 的复现。

## 本机复现

测试环境：Windows 上的 WSL Ubuntu，内核 `6.6.87.2-microsoft-standard-WSL2`，x86_64、glibc 2.39；Python 3.12.3，PyTorch 2.6.0+cpu、pytest 8.3.5。测试临时目录位于 ext4，页大小与 `statvfs.f_bsize` 均为 4096。测试依赖放在独立目录 `E:\work\SSD\research\linux-test-deps`，没有修改项目 requirements/锁文件，也没有升级用户已有运行环境。编译了本基线的 CPU common native 扩展供 Bitmap/event notifier 使用，没有加入假的 native 替身。

在已准备的 WSL 环境中：

```bash
cd /mnt/e/work/SSD/LMCache-pr
export PYTHONPATH=/mnt/e/work/SSD/research/linux-test-deps:/mnt/e/work/SSD/LMCache-pr
export LMCACHE_TRACK_USAGE=false
python3 -m pytest --confcutdir=tests/v1/distributed -q \
  tests/v1/distributed/test_fs_l2_adapter_staging.py \
  tests/v1/distributed/test_fs_l2_adapter_delete.py \
  tests/v1/distributed/test_fs_l2_adapter_keys.py
python3 -m ruff check lmcache/v1/distributed/l2_adapters/fs_l2_adapter.py tests/v1/distributed/test_fs_l2_adapter_staging.py
python3 -m isort --check-only lmcache/v1/distributed/l2_adapters/fs_l2_adapter.py tests/v1/distributed/test_fs_l2_adapter_staging.py
python3 -m mypy --config-file=pyproject.toml lmcache/v1/distributed/l2_adapters/fs_l2_adapter.py tests/v1/distributed/test_fs_l2_adapter_staging.py
python3 -m codespell_lib --toml pyproject.toml lmcache/v1/distributed/l2_adapters/fs_l2_adapter.py tests/v1/distributed/test_fs_l2_adapter_staging.py
```

`--confcutdir` 只加载 distributed 测试范围内的 fixtures，避免顶层 GPU 相关测试环境设置；没有修改仓库 conftest。此前环境准备阶段因缺少 native 扩展/numba/py-cpuinfo/aiohttp 发生的收集失败不是产品测试通过；依赖补齐后才取得上面的 56 项结果。

未运行全仓测试、GPU/NPU 集成、完整服务关闭或性能测试。未修改依赖、controller、vLLM、Ascend 插件，也未提交远端 PR。后续只在版本对账存在实质差异或记录缺项时补受影响的验证。
