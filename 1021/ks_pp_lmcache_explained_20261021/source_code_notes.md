# 完成通知怎样搭上模型结果：六组真实源码与白话解释

纯本地报告材料；只从已归档的当前实际运行源码摘录，不修改推理源码、服务或配置，不新增设备实验。

先计算请求指C4中先进入正计算的第3条；缓存已恢复请求主要指第2/第4条。代码里的request_id可以是另一条请求，完成通知不必属于正在执行模型的请求。

六组均为actual_source_excerpt。每组8–20行，片段保持来源原文与缩进；多个fragment之间明确有省略，不能将其当作可独立运行文件。解释和教学伪代码不混进真实代码块。当前运行源码含本轮_ks观测调用，原样保留。

## 先认清七个词

- **worker / rank**: 一个执行模型分片的工作进程及其编号。本次P有16个rank；rank不是请求编号。
- **PP前段 / 后段**: 模型被分成先后两段；本次rank0–7执行前段，rank8–15执行后段。
- **device event**: 设备上的完成标记。query看见ready，表示检查时事件已就绪；它不是物理完成的精确时间戳。
- **Future**: 稍后才能得到的结果凭证。它可以已经收到消息、仍等设备，也可以设备已就绪、但核心还没消费结果。
- **KVConnectorOutput**: worker带回核心的缓存控制信息袋，包括收发完成请求、错误块和统计；不是KV数据本身。
- **scheduler**: 决定下一批让哪些请求计算多少token的调度器。worker知道缓存好了，不等于scheduler已经知道。
- **零token批**: 本轮没有模型token要算的批次，仍可传递缓存metadata和完成信息。本次不能把所有零批都称为纯轮询，也不能把它们算成重复READ。

## 1. 设备future怎样确认ready

实际函数: `DeviceMessagingFuture.query`

excerpt：展示query完整方法；被调用的_on_raw_future_complete负责取得响应并导入event，其方法体未在此重复展示。

`/dev/shm/ks-ttft-prepare-20261020/runtime/lmcache/v1/multiprocess/futures.py` L371-L389 (actual source excerpt)

```python
def query(self) -> bool:
        """
        Check if the future is done.

        Returns:
            bool: True if the future is done, False otherwise.
        """
        if self._raw_response_processed:
            if self.event_ is None:
                return True
            return self._event_backend.query_event(self.event_)

        if self.raw_future_.query():
            self._on_raw_future_complete()
            if self.event_ is None:
                return True
            return self._event_backend.query_event(self.event_)

        return False
```

**关键行**

- L383: 先确认底层消息结果已经回来，才有资格处理它携带的设备event。
- L384: 取出响应、导入返回的event，并记住响应已经处理。
- L381, L387: 有event时还要向设备后端查询就绪；不是只看RPC响应。

**逐步读**

1. 底层响应没回来时，返回False。
2. 响应回来后，先处理响应中的设备event。
3. 存在event就查询设备event；没有event的分支直接返回True，两种语义不能混读。
4. 本次64条真实ready记录的event_id均非空、raw_response_processed均为True，future/raw id均与对应提交精确匹配。

**能证明**: 本次实际DeviceMessagingFuture在有event的路径中会检查设备就绪，不能将其完成报告降格成纯RPC ACK。

**不能据此证明**: 不能给出DMA/event物理完成的精确时刻；事件可能更早完成，到轮询时才被发现。 不能把handler的主机结束时间当作设备完成时间。

当前源码只读副本: [kv-future-and-binary-metadata.json](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/kv-future-and-binary-metadata.json)

## 2. worker怎样把ready收进完成集合

实际函数: `LMCache adapter.get_finished（retrieve循环）`

excerpt：只展示retrieve循环的查询、结果确认和错误块处理；省略store、清理跟踪字典、返回集合及日志细节。

`/dev/shm/ks-ttft-prepare-20261020/runtime/lmcache/integration/vllm/vllm_multi_process_adapter.py` L2162-L2174 (actual source excerpt)

```python
        for request_id, (r_future, r_block_ids) in self.retrieve_futures.items():
            _ks_ready = r_future.query()
            _ks_completion_query(self, request_id, r_future, "RETRIEVE", _ks_ready)
            if not _ks_ready:
                continue

            with _ks_completion_result(self, request_id, r_future, "RETRIEVE"):
                r_result = r_future.result(timeout=60)
            finished_retrieves.add(request_id)

            if not r_result:
                self.error_block_ids.update(r_block_ids)
                self._failed_request_ids.add(request_id)
```

**关键行**

- L2163: 询问缓存已恢复请求的future是否ready。
- L2169: ready之后仍取结果；DeviceMessagingFuture的该路径还会等待/同步event。
- L2170: 将请求放进本轮收集到的恢复完成集合。
- L2173: 完成还可能携带失败状态，错误块不能在提前通知方案中丢掉。

**逐步读**

1. 遍历正在恢复缓存的请求，逐个query。
2. 未ready就继续等待后续轮询；ready才取result。
3. 把已确认结果的请求加入finished_retrieves，失败时同时标记错误块。
4. 上层mixin在本次前向context的finally里调用get_finished，再把集合装入KVConnectorOutput；因此先计算请求的输出袋里可能装着另一条请求的完成通知。

**能证明**: 设备future就绪、worker收集完成集合、核心消费集合是不同动作。 defer_finalize只延后wait_for_save/clear metadata，本次仍在draft之前收集get_finished。

**不能据此证明**: 收集到finished_retrieves不等于核心或scheduler已经收到。 不证明以后增加轮询线程是安全或必要的；本次候选优先复用已有载荷。

当前源码只读副本: [actual-kv-readiness-source.json](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/cache/actual-kv-readiness-source.json)

## 3. 最后PP段先返回None，sample再带回同一袋KV信息

实际函数: `Ascend runner.execute_model / sample_tokens（本次生成路径）`

excerpt：三处原文，中间明确省略；省略前向状态保存、采样/MTP/late finalize及构造器后续字段，代码没有改写。这里的execute None是该路径的协议行为，不是缓存未命中或sample失败。

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm_ascend/worker/model_runner_v1.py` L2409-L2416 (actual source excerpt)

execute_model继续走到最后PP段的路径；前文非最后段已返回IntermediateTensors。

```python
            self.kv_connector_output = kv_connector_output

        # Now the batch has been launched we can wait for corrections from the
        # previous model forward without breaking async scheduling.
        if deferred_state_corrections_fn:
            with _ks_span('runner_detail.deferred_corrections'):
                deferred_state_corrections_fn()
        return None
```

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm_ascend/worker/model_runner_v1.py` L2423-L2424 (actual source excerpt)

sample_tokens入口取出之前保留的KV输出对象。

```python
        kv_connector_output = self.kv_connector_output
        self.kv_connector_output = None
```

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm_ascend/worker/model_runner_v1.py` L2550-L2556 (actual source excerpt)

在sample构造模型返回值时携带KV输出；构造器后续字段在excerpt之外。

```python
        model_runner_output = ModelRunnerOutput(
            req_ids=req_ids_output_copy,
            req_id_to_index=req_id_to_index_output_copy,
            sampled_token_ids=valid_sampled_token_ids,
            logprobs=logprobs_lists,
            prompt_logprobs_dict=prompt_logprobs_dict,
            kv_connector_output=kv_connector_output,
```

**关键行**

- L2409: 把已经收集好的KV控制信息保存在runner对象中。
- L2416: 本次最后PP段execute并不在这里交付最终ModelRunnerOutput。
- L2423: 后续sample取回之前的KV对象。
- L2556: sample的模型输出才把这个KV对象带回结果链。

**逐步读**

1. 前段execute会返回中间激活和KV控制信息；最后段生成路径保留KV对象后先返回None。
2. 随后sample取回同一个KV对象，再进行采样、MTP和late finalize等动作。
3. sample的ModelRunnerOutput携带KV控制信息。前段execute与sample也可能镜像带回同一份完成集合。
4. 因此缓存已恢复请求的通知，可以搭在先计算请求的execute/sample结果上。

**能证明**: 通知已被worker收集，仍可能因为承载方式而等到sample结果才能进入核心聚合。

**不能据此证明**: 不能据此说MTP是实际设备耗时主因；承载等待与MTP实际工作量是两类问题。 不说明所有runner/池化/其他配置都按这个分支返回None。

当前源码只读副本: [current-native-and-kv-source.json](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/current-native-and-kv-source.json)

## 4. 聚合器为什么跳过execute里的前段完成信息

实际函数: `KVOutputAggregator.aggregate`

excerpt：三处原文，省略计数helper定义、统计/metadata聚合等。门禁在遍历所有worker之前，不能把它解释成只跳过那个空rank。

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/distributed/kv_transfer/kv_connector/utils.py` L66-L72 (actual source excerpt)

```python
    def aggregate(
        self, outputs: list[ModelRunnerOutput | None], output_rank: int = 0
    ) -> ModelRunnerOutput | None:
        if not outputs[output_rank]:
            return None

        # Aggregate kv_connector_output from all workers
```

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/distributed/kv_transfer/kv_connector/utils.py` L94-L98 (actual source excerpt)

```python
        for model_runner_output in outputs:
            assert model_runner_output is not None
            kv_output = model_runner_output.kv_connector_output
            if not kv_output:
                continue
```

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/distributed/kv_transfer/kv_connector/utils.py` L115-L117 (actual source excerpt)

```python
            update_finished_set(
                kv_output.finished_recving, self._recv_remaining_count, finished_recving
            )
```

**关键行**

- L69: 先看被指定为主要输出的rank是否给了模型输出。
- L70: 若为空，整次聚合直接返回；下面所有worker的KV完成信息都不会计数。
- L94: 只有通过门禁，才开始逐worker处理KV输出。
- L116: 将收到的完成集合累入剩余份数计数器。本次需要16份，不是所有部署固定16。

**逐步读**

1. 本次PP2指定的主要输出rank是8，前段为0–7。
2. 最后段execute返回None时，门禁立即返回，前段execute已有的KV完成袋也没进入计数。
3. sample的主要输出非空后，才遍历全部worker，把前段剩余报告补齐。
4. 这解释了本次后段8份早已计数、前段8份却等sample；同类门禁PP1也存在，不能叫PP独有协议。

**能证明**: 本次前段报告可以被selected-output门禁延后，而不是一定仍在拷贝。

**不能据此证明**: 直接移动这句门禁就能安全修复。真实CPU负对照已显示：sample镜像未在计数前去重，会留下错误的残余计数。 不能绕过错误块、所需rank份数或请求生命周期约束。

当前源码只读副本: [current-native-and-kv-source.json](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/current-native-and-kv-source.json)

## 5. 核心怎样读齐所有worker响应

实际函数: `MultiprocExecutor.collective_rpc / nested get_response`

excerpt：两处原文。中间省略aggregate绑定和发送设置；完整方法在启用KV聚合时令output_rank=None，保留response_mqs全列表，而不是只读主要输出rank。异常处理保留在展示的循环中。

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/v1/executor/multiproc_executor.py` L368-L369 (actual source excerpt)

```python
        if kv_output_aggregator is not None:
            output_rank = None
```

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/v1/executor/multiproc_executor.py` L388-L404 (actual source excerpt)

```python
        def get_response():
            responses = []
            for mq in response_mqs:
                dequeue_timeout = (
                    None if deadline is None else (deadline - time.monotonic())
                )
                try:
                    status, result = mq.dequeue(timeout=dequeue_timeout)
                except TimeoutError as e:
                    raise TimeoutError(f"RPC call to {method} timed out.") from e
                if status != WorkerProc.ResponseStatus.SUCCESS:
                    raise RuntimeError(
                        f"Worker failed with error '{result}', please check the"
                        " stack trace above for the root cause"
                    )
                responses.append(result)
            return responses[0] if output_rank is not None else responses
```

**关键行**

- L369: 启用KV聚合后，RPC需要全部worker回复。它与聚合器内部选择哪个output_rank是两层不同设置。
- L390: 按各worker响应队列的既定顺序逐个读取。
- L395: 队列没结果时会等待；前一个队列阻塞时，后面的队列即使可能已有消息，也还没读到。
- L403: 先收集所有响应，整个get_response返回之后才执行聚合回调。

**逐步读**

1. KV聚合开启后，执行器保留所有响应队列。
2. get_response逐队列读取并检查成功/失败。
3. 全部响应收齐后返回列表，FutureWrapper才调用aggregate。
4. worker方法返回只说明载荷构造完成；当前输出链还可经过async_output_queue再进响应MQ，不能直接当作MQ已可读。

**能证明**: 现有通知会受完整响应收集路径约束。 候选的更小入口是读取到前段execute载荷时复制完成信息，而不是新增worker轮询。

**不能据此证明**: 本次所有前段消息在worker.end时已可读；缺实际响应MQ入队/出队时刻。 不能让多个reader同时争读同一response MQ，否则可能错配命令回复。

当前源码只读副本: [current-cpu-source-and-native-maps.json](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/current-cpu-source-and-native-maps.json)

## 6. 为什么只加通知inbox仍然不够

实际函数: `EngineCore.step_with_batch_queue`

excerpt：两处原文，中间省略None/异常处理。完整代码会保留execute异常，不是吞掉错误。前段execute的合法None与此处sample最终结果异常的None不要混为一谈。

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/v1/engine/core.py` L562-L570 (actual source excerpt)

```python
        # Block until the next result is available.
        future, scheduler_output, exec_model_fut = batch_queue.pop()
        _ks_core_mark_batch("core.batch_dequeued", scheduler_output, future_id=id(future), raw_future_id=id(exec_model_fut), core_batch_queue_depth=len(batch_queue))
        with (
            self.log_error_detail(scheduler_output),
            self.log_iteration_details(scheduler_output),
        ):
            with _ks_batch_span('core.output_future_wait', scheduler_output, core_wait_future_id=id(future), core_batch_queue_depth=len(batch_queue)):
                model_output = future.result()
```

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/v1/engine/core.py` L580-L585 (actual source excerpt)

```python
        with _ks_batch_span('core.process_aborts', scheduler_output, core_batch_queue_depth=len(batch_queue)):
            self._process_aborts_queue()
        with _ks_batch_span('core.update_from_output', scheduler_output, core_batch_queue_depth=len(batch_queue)):
            engine_core_outputs = self.scheduler.update_from_output(
                scheduler_output, model_output
            )
```

**关键行**

- L563: 取出一批准备等待；从deque取出并不意味着该批已经完成或释放名额。
- L570: 核心主线程在这里同步等待，等待期间不会自动再次进入scheduler。
- L581: 完整模型结果回来后，先处理期间发生的中止。
- L583: 随后才按顺序将模型结果交给scheduler。

**逐步读**

1. 核心取出队首批次并等待它的完整结果。
2. 当前FutureWrapper.result还会按命令顺序排空排在前面的Future；本次先等同一先计算请求的execute，再等它自己的sample。
3. 主线程阻塞时，即使另建inbox收到通知，也不会自动重新调度缓存已恢复请求。
4. 因此候选需要分离响应读取推进与核心整批结果等待，并让核心可被控制通知唤醒；scheduler仍由核心单线程修改。

**能证明**: 只提前写ready集合或只加inbox不足以保证提速。 通知推进、模型结果顺序和批容量必须一起考虑。

**不能据此证明**: 提前知道ready就有合法计算名额；本例先计算批与零token控制批已共占两份逻辑名额。 不能从本文源码片段推出几行补丁即可修完或能节省多少毫秒。

当前源码只读副本: [current-cpu-source-and-native-maps.json](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/current-cpu-source-and-native-maps.json)

## 另外两道边界：容量与输出队列

### 本次队列容量为什么是2

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/config/vllm.py` / `max_concurrent_batches`

本次V1 runner、PP2时，async开或关均返回pp_size=2。core.py:194–202按该值建立batch_queue，220–222据是否存在队列选择step_with_batch_queue。关闭async并不会据此消除PP批队列。

### worker.end还不是响应MQ可读

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/v1/executor/multiproc_executor.py` / `WorkerProc.handle_output / enqueue_output / async_output_busy_loop`

当前P8 handle_output:949可先放入async_output_queue；async_output_busy_loop:959再取出；enqueue_output:934最终放入response MQ。故worker方法已返回与MQ实际可读之间仍有未观测边。

### 收集位置在前向finally，不是MTP后才第一次查

`/dev/shm/ks-ttft-prepare-20261020/runtime/vllm/v1/worker/kv_connector_model_runner_mixin.py` / `_get_kv_connector_output / finalize_kv_connector`

finally里调用get_finished；defer_finalize=True仍然收集，只延后wait_for_save和clear metadata。不能把后续通知晚到解释成等MTP后才第一次查询。

## 独立教学伪代码：不是运行补丁

### 教学伪代码：本次当前路径

**教学伪代码，不是实际源码，不可当作可执行补丁；只描述本次已闭合的C4路径。**

```text
零token控制往返先把缓存已恢复请求的后段8份完成信息交给核心
核心提交先计算请求的 execute，再提交它的 sample
前段worker结束前向时，query发现缓存已恢复请求的其余完成信息
前段把这些完成信息装进先计算请求的execute返回值
核心同步等待先计算请求的完整结果
  先收齐它的execute全部worker响应
  主要输出rank返回None，整次KV聚合被跳过
  再等它的sample；sample镜像带回此前的KV信息
  这时前段剩余8份才被核心计数
核心把完整完成集合交给scheduler，再判断下一批能否入场
```

恢复完成是另一条请求的控制状态，但当前状态的交付时机依附在先计算请求的模型结果链上。

### 教学伪代码：候选解耦边界

**教学伪代码，不是实际源码或可执行补丁；DESIGN_ONLY，尚未应用或通过真实生命周期/性能验收。**

```text
唯一响应读取者：
  仍按原命令顺序读取每条worker响应MQ并推进Future
  读到前段execute载荷时，复制缓存已恢复请求的完成记录到inbox
核心线程：
  等待通知可消费，或队首完整模型输出已完成
  只在本线程按请求代次和rank去重，处理完成及错误信息
  完整模型输出仍按原顺序更新scheduler
  仅在报告齐、KV安全且预算合法时考虑提交缓存已恢复请求
预算是另一个设计选择：
  可研究计算批与控制批分别记账，但不是清空原队列
  控制批仍保留Future、命令顺序、错误和所持资源
  若坚持原总在途上限2不变，先计算批加控制批占满时仍不能再发一批
```

single reader与可唤醒core等待缺一不可；由reader接管Future推进后，core不能再同步争读同一MQ/排空同一Future队列。模型输出顺序与scheduler单线程仍保留。

## 候选实现的边界

- 现有后段8份已经真实计数，本例优先复用前段execute载荷，不新建后段/allrank通知IPC，也不新增get_finished轮询线程。
- 提前处理通知必须在计数前去重sample镜像，并保留错误块、send完成、worker metadata及KV生命周期。
- 计算/控制分别记账会改变原来两者共用2名额的入场政策；若允许先计算批、控制批、下一计算批同时在途，总数可成为3，不能声称原总容量未变。
- 零token控制批可能仍搬运或持有资源；需要证明具体控制批的动作与退休条件，不能假退休或删除Future。
- 该方向不要求改D/Mooncake、17行兼容或DP/TP/PP/MTP，但尚未形成运行修复或收益验收。

## 证据入口

- [最终通知链与C1设备活动小结](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/notification/C4通知链与C1设备活动小结.md)
- [64条真实query与worker/core身份闭合](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/notification/c4-ready-query-chain.json)
- [当前方法隔离CPU验收及限制](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/notification/c4-carrier-cpu-receipt.json)
- [零token入场、覆盖边界与当前容量源码](E:/work/ks/output/ks_rootcause_window_20261021/analysis/continuation_1730/notification/c4-zero-token-admissions.json)

## 摘录核对

六组实际摘录分别为19、13、17、15、19、15行，每一行及关键行位置都已与当前源码只读副本核对。这里只做材料提取与核对，没有导入推理框架、使用NPU、远程操作或修改运行源码；机器可读记录保存在source_code.json。
