(() => {
  'use strict';
  const read = id => JSON.parse(document.getElementById(id).textContent);
  const data = read('report-data');
  const sources = read('source-data');
  const commands = read('commands-data');
  const glossary = read('glossary-data');
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (value, places = 2) => value == null ? 'NOT_VERIFIED' : Number(value).toLocaleString('zh-CN', {minimumFractionDigits: places, maximumFractionDigits: places});
  const signed = (value, places = 2) => `${value > 0 ? '+' : ''}${num(value, places)}`;
  const fileLink = path => path ? `file:///${path.replace(/\\/g, '/')}` : '';
  const link = (path, label) => `<a href="${esc(fileLink(path))}">${esc(label)}</a>`;
  const list = values => `<ul>${values.map(v => `<li>${esc(v)}</li>`).join('')}</ul>`;

  let flowIndex = 0;
  let hit = 'full';
  const hitText = () => hit === 'full' ? {left:1, cached:4095, read:'完整4096个token对应的KV（按块恢复）', calc:'最后1个输入token'} : hit === 'partial' ? {left:2048, cached:2048, read:'前2048个token对应的KV', calc:'剩余2048个token'} : {left:4096, cached:0, read:'无可复用KV', calc:'完整4096个token'};
  const flowSteps = () => {
    const h = hitText();
    const hot = hit !== 'miss';
    return [
      {title:'请求进入P',off:['分词、排队、分配本次工作','4096个输入token'],on:['分词、排队、分配本次工作','与OFF相同的模型与用例'],why:'调度器决定哪些请求能在这一批执行。代理、分词与排队也会影响首token时间，但它们不是LMCache所省的Attention计算。',code:'Scheduler / ModelRunner'},
      {title:'查历史KV',off:['没有LMCache外部查找','本图只讨论外部LMCache路径'],on:['查询LMCache命中前缀',hot ? `教学情景：可复用${h.read}` : '未命中，仍付出查找动作'],cls:'extra',why:'LMCache按输入前缀和缓存块查找历史结果。查询成功只表示缓存里有数据，还不表示数据已经恢复到模型worker的KV内存。',code:'LMCacheMPConnector lookup'},
      {title:'恢复历史KV',off:['不从LMCache恢复','完整输入由本次模型计算'],on:[hot ? '每个rank恢复自己负责的KV' : '无历史数据可恢复',hot ? `${h.read} → 本段模型KV内存` : '不能凭空跳过输入计算'],cls:hot ? 'extra' : 'empty',why:hot ? '前41层与后37层各有自己的KV，16个worker各处理本rank的分片。多份rank分片和512-token分块，不等于整份历史KV被反复完整加载。' : '冷ON仍包含缓存查询及后续保存；所以冷ON不能替代真正关闭LMCache的OFF对照。',code:'retrieve() / native transfer / device event'},
      {title:'告诉调度器：恢复好了',off:['没有外部恢复完成通知','仍有正常模型结果调度'],on:[hot ? 'event ready → 收集16份完成状态' : '无需等待本次外部READ完成',hot ? '核心收齐后，才允许该请求继续计算' : '沿未命中路径继续执行'],cls:hot ? 'extra' : 'empty',why:'设备event已经ready、worker把状态放入返回值、核心收到并计齐，是三个时刻。并发时，完成信息会搭载在另一条正在计算请求的execute/sample响应上，可能延后到达核心；这不是新的KV复制。',code:'future.query → get_finished → aggregator → scheduler'},
      {title:'前41层计算',off:['4096个token通过前41层','保留本段新KV'],on:[`${h.calc}通过前41层`,hot ? '利用已恢复历史KV，省去前缀重算' : '与OFF同样要算完整输入'],cls:hot ? 'saved' : '',why:'命中后，跳过的是历史token的大量逐层计算，不是跳过模型的层。最后仍需计算的token要经历Attention、线性变换、专家等本模型实际路径；主机调用这些层的动作仍在。',code:'model forward / Attention / MoE'},
      {title:'PP前后段交接',off:['前41层 → 后37层','传递本次计算的中间激活'],on:['前41层 → 后37层','仍需中间激活，数据规模可能更小'],why:'这里是PP的阶段依赖：后37层要等本次token的中间计算结果。它传递的是隐藏状态，不是把LMCache缓存从前段转交给后段。缓存命中并不能取消这条依赖。',code:'PP send / recv intermediate tensors'},
      {title:'后37层计算',off:['4096个token通过后37层','形成完整模型结果'],on:[`${h.calc}通过后37层`,hot ? '使用后段自己恢复的历史KV' : '仍需完整输入计算'],cls:hot ? 'saved' : '',why:'后段的历史KV与前段的历史KV属于不同模型层。后段在自己负责的层读取历史上下文，处理从前段收到的中间激活；它没有重新执行前41层。',code:'last PP rank model forward'},
      {title:'取得输出与采样结果',off:['输出分数、采样及既有MTP流程','得到首输出及本批模型结果'],on:['输出分数、采样及既有MTP流程','完整命中也不能省略全部输出处理'],why:'KV不是完整输出答案。最后一个输入token仍需通过全部模型层，得到最终隐藏状态和词表分数，才能按当前规则产生输出。当前版本最后PP段还可能把KV状态留到sample响应再返回。',code:'logits / sample / MTP / finalize'},
      {title:'保存缓存副本',off:['没有LMCache STORE','模型仍正常持有本次KV'],on:[hot ? '按需保存新增部分' : '把可缓存KV保存到CPU缓存','STORE与计算、传输可能重叠'],cls:'extra',why:'STORE是额外的缓存动作，具体保存范围取决于命中和块边界。保存一个副本不等于把当前模型KV从NPU释放。此节点不是声明所有STORE都阻塞在P→D之前。',code:'LMCache STORE / completion event'},
      {title:'P向D交付KV',off:['原Mooncake P→D传输','D使用P已经算好的KV'],on:['仍走原Mooncake P→D','LMCache仅在P侧；D不全量重算'],why:'本性能主线保留原来的P到D传输路线。把D全量重新计算输入算作“接入成功”，会破坏对照；早期双侧MP的回退结果不用于这里的性能结论。',code:'MooncakeConnectorV1'},
      {title:'D继续生成，客户端收流',off:['D原形态继续生成256个token','观测TTFT、后续间隔和总耗时'],on:['D计算形态、MTP、用例保持','P省下的时间不保证等量传到端到端'],why:'TTFT涵盖前面的排队、P、传输和D首输出。后续TPOT与总耗时还受D、调度和请求时序影响。不能把端到端减TTFT解释成纯D计算时间。',code:'D decode / streaming response'}
    ];
  };
  function renderFlow() {
    const steps = flowSteps();
    $('scenario-copy').textContent = hit === 'full' ? '完整命中示意：按块恢复覆盖4096 token的KV，但调度器保留最后1 token重算，因此省掉约4095 token的计算。恢复覆盖量、省算token数、命中率统计口径不是同一件事。' : hit === 'partial' ? '部分命中示意：复用前2048 token的历史KV，后2048 token继续计算；这不是本轮实测50%命中率。' : '未命中示意：完整输入仍要计算，并增加外部查找与可缓存数据保存。';
    for (const side of ['off','on']) {
      $('flow-'+side).innerHTML = steps.map((step,i) => `<button type="button" data-flow="${i}" class="flow-node ${side === 'on' ? step.cls || '' : ''} ${flowIndex === i ? 'active' : ''}" aria-pressed="${flowIndex === i}"><b>${i+1}. ${esc(step[side][0])}</b><small>${esc(step[side][1])}</small></button>${i < steps.length-1 ? '<div class="flow-arrow" aria-hidden="true">↓</div>' : ''}`).join('');
    }
    const step = steps[flowIndex];
    $('flow-count').textContent = `${flowIndex+1} / ${steps.length} · ${step.title}`;
    $('flow-explanation').innerHTML = `<span class="badge ${step.cls === 'extra' ? 'source' : 'fact'}">第${flowIndex+1}步 · 动作解释</span><h3>${esc(step.title)}</h3><p>${esc(step.why)}</p><div class="pair-label">对应的代码职责</div><p><code>${esc(step.code)}</code></p><p class="fine">这些是单请求依赖顺序；实际并发执行、STORE、通知与其他请求可以交错，图中节点不是可相加的计时段。</p>`;
    $('flow-prev').disabled = flowIndex === 0;
    $('flow-next').disabled = flowIndex === steps.length-1;
  }
  document.querySelectorAll('[name=hit]').forEach(el => el.addEventListener('change', () => {hit=el.value;renderFlow();}));
  $('flow-prev').addEventListener('click',()=>{flowIndex--;renderFlow();});
  $('flow-next').addEventListener('click',()=>{flowIndex++;renderFlow();});
  document.querySelector('.flow-pair').addEventListener('click', e=>{const el=e.target.closest('[data-flow]');if(el){flowIndex=Number(el.dataset.flow);renderFlow();}});

  // Self-contained SVGs: dimensions match the visible container so mobile labels remain legible.
  function bars(id, rows, unit, title) {
    const width = Math.max(290, $(id).clientWidth-30);
    const height = 250;
    const left = width < 500 ? 112 : 155;
    const right = width < 500 ? 66 : 103;
    const plot = width-left-right;
    const maximum = Math.max(...rows.map(x=>x.value)) * 1.05 || 1;
    const start = 57, gap = rows.length > 3 ? 44 : 69;
    let svg = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title><text x="12" y="20" class="svg-label">${esc(unit)} · 越短越快</text>`;
    for(let i=0;i<=4;i++){
      const x=left+plot*i/4;
      svg+=`<line x1="${x}" y1="36" x2="${x}" y2="${start+gap*(rows.length-1)+24}" stroke="#e6ebf3"/><text x="${x}" y="${start+gap*(rows.length-1)+45}" text-anchor="middle" class="svg-small">${num(maximum*i/4, maximum < 10 ? 1 : 0)}</text>`;
    }
    rows.forEach((row,i)=>{
      const y=start+gap*i;
      svg+=`<text x="${left-10}" y="${y+6}" text-anchor="end" class="svg-label">${esc(row.label)}</text><rect x="${left}" y="${y-11}" width="${Math.max(1,plot*row.value/maximum)}" height="26" rx="4" fill="${row.color || '#6d93cd'}"/><text x="${left+plot+9}" y="${y+6}" class="svg-value">${num(row.value,row.places ?? (maximum>100?1:2))}</text>`;
    });
    $(id).innerHTML=svg+'</svg>';
  }
  const keyMetric = {ttft:'ttft_s',tpot:'tpot_ms',e2el:'e2el_s'};
  function selectedPairs(){const tier=Number($('tier-select').value);return ['PP2','PP1'].map(layout=>data.historical_three_tier_pairs.filter(x=>x.layout===layout)[tier]);}
  function renderResults(){
    const pairs=selectedPairs();
    const key=keyMetric[$('metric-select').value];
    const factor=key==='ttft_s'?1000:1;
    const unit=key==='e2el_s'?'秒':'毫秒';
    const rows=pairs.flatMap(pair=>{const m=pair.metrics.find(x=>x.key===key);return [{label:`${pair.layout} · OFF`,value:m.off_mean*factor,color:'#a7b9d1'},{label:`${pair.layout} · ON`,value:m.on_mean*factor,color:pair.layout==='PP2'?'#3c70bb':'#429278'}];});
    bars('metric-chart',rows,unit,$('metric-select').selectedOptions[0].textContent);
    $('metric-caption').textContent = pairs.map(p=>{const m=p.metrics.find(x=>x.key===key);return `${p.layout}：ON相对OFF ${signed(m.relative_percent)}%，共同成功${p.n}对`;}).join('；')+'。负值表示耗时减少；跨形态还有TP、CP等差异。';
    const defs=data.historical_c4_matrix.metrics;
    const valuesFor = k => pairs.flatMap(p=>{
      const m=p.metrics.find(x=>x.key===k);
      if(m)return[m.off_mean,m.on_mean];
      return ({input_tokens:[p.input_tokens,p.input_tokens],output_tokens:[p.output_tokens,p.output_tokens],max_concurrency:[p.concurrency,p.concurrency],formal_attempts:[p.attempts_per_side,p.attempts_per_side],common_success:[p.n,p.n],successes:[p.off_success,p.on_success],external_hit_percent:[null,p.on_hit_percent],pure_prefill_ms:[null,null],d_decode_ms:[null,null]})[k] || [null,null];
    });
    const formatted=(k,v)=>{
      if(k==='external_hit_percent')return v==null?'未启用':`${num(v)}%`;
      if(v==null)return '<span class="badge unknown">未采得同口径数据</span>';
      if(['input_tokens','output_tokens','max_concurrency','formal_attempts','common_success','successes'].includes(k))return num(v,0);
      return num(v,k==='tpot_ms'?3:4);
    };
    $('metric-matrix').innerHTML='<thead><tr><th scope="col">指标（纵轴）</th>'+['PP2 · 不接LMCache','PP2 · P侧接LMCache','PP1 · 不接LMCache','PP1 · P侧接LMCache'].map(x=>`<th scope="col">${esc(x)}</th>`).join('')+'</tr></thead><tbody>'+defs.map(d=>`<tr><th scope="row">${esc(d.label)}${['ttft_s','e2el_s','decode_s'].includes(d.key)?'（秒）':d.key==='tpot_ms'?'（毫秒）':''}</th>${valuesFor(d.key).map(v=>`<td>${formatted(d.key,v)}</td>`).join('')}</tr>`).join('')+'</tbody>';
    $('matrix-note').textContent='共同成功配对分别在PP2组、PP1组内建立，不是四列共用同一组请求。命中率以全部正式尝试为分母；延迟均值只取组内共同成功对。OFF的“未启用”不写成0%缓存命中。';
    $('result-method').innerHTML=`<p>历史正式结果：${pairs.map(p=>`${p.layout}为${esc(p.date)}、${p.attempts_per_side}次正式尝试、OFF成功${p.off_success}、ON成功${p.on_success}、共同成功${p.n}对`).join('；')}。</p><p>4K单并发外部命中率只有6.25%，8K单并发为18.75%；低命中率整档均值不能代表完整命中后的纯收益。4K四并发的100%命中也不是100%设备工作可省略。</p><p>过滤失败避免将失败零值记入延迟，但不能消除失败对闭环负载、其他请求时序的间接影响。这些是单轮历史测量，不提供稳定性或显著性保证。</p><p>今天另收22条独立ON诊断，均完成256输出token，其中4条为同一已缓存4K输入的并发诊断；它们用于机制定位，不冒充原32条四并发用例，也没有新跑OFF。</p>${actionBudget()}`;
  }

  function renderSfa(){
    const obs=data.sfa_device_observations;
    const a=obs.find(x=>x.layout==='PP1'&&x.input_state==='cold'&&x.label.includes('8K'));
    const b=obs.find(x=>x.layout==='PP2'&&x.input_state==='cold'&&x.label.includes('8K')&&Number(x.rank)===7);
    bars('sfa-chart',[{label:'PP1 · 冷8K',value:a.per_call_median_ms,color:'#9d82ba',places:3},{label:'PP2 · 冷8K重卡',value:b.per_call_median_ms,color:'#428e77',places:3}],'每次SFA设备执行毫秒','冷8K同模板SFA的设备单次执行中位数：PP1与PP2');
  }
  document.querySelector('.prep-grid.many').innerHTML='<i></i>'.repeat(64);
  document.querySelector('.prep-grid.fewer').innerHTML='<i></i>'.repeat(8);

  const events=[
    {t:135.308,short:'后段已报',title:'后37层的8份完成状态，已经被核心计入',body:'以四并发中一条等待恢复完成的请求为例，后段8个rank最迟约135ms已被核心计数。但完整请求要等16份，前段的另外8份还没有到齐。不能把局部完成当成全模型KV已完成。'},
    {t:367.043,short:'前段ready',title:'前41层也已看到自己的设备event就绪',body:'前段的设备query返回ready=true；随后worker收集完成状态。两条关键请求的query到worker上报仅约0.63–1.36ms，所以218–240ms的大段等待发生在已经观察到设备就绪之后。event可能比轮询发现的时刻更早完成。'},
    {t:369.265,short:'载荷构造好',title:'前段的execute返回载荷，已经包含完成信息',body:'全部前段相关worker方法至此已返回，载荷包含等待请求的KV完成记录。这里证明的是载荷构造完，不是MQ已经可读；响应输出线程、入队、核心读取这几段还缺直接时间戳。'},
    {t:551,short:'后段返回空',title:'后段execute暂时返回None，整次聚合被跳过',body:'当前后段要延后完成采样/MTP及结果处理。聚合器先检查指定输出rank：若它返回None，就不继续聚合前段execute中已经携带的KV完成信息。后续sample会镜像带回这些信息。约551ms是后段execute结束附近的定位点，不是可回收收益。'},
    {t:585.332,short:'核心收齐',title:'等sample返回，核心才把前段另外8份计齐',body:'同一个先计算批的sample返回后，核心才完成计数，约586.5ms更新调度状态。不是在等无关的旧批；是另一条缓存已恢复请求的控制消息，搭载在当前模型批的结果链上。'},
    {t:608.336,short:'下一批计算',title:'约589ms提交下一批，约608ms开始前41层',body:'通知交给调度器后，还要满足在途批与KV生命周期等条件。本轮观测到下一批约589ms提交、608ms进入前段计算。提前通知只是候选修复的一环，尚未证明可把这些时刻整体提前218–240ms。'}
  ];
  let timelineStep=0;
  function renderTimeline(){
    const w=1060,h=358,left=195,right=34,plot=w-left-right;
    const x=t=>left+t/650*plot;
    const bar=(s,e,y,color,label)=>`<rect x="${x(s)}" y="${y}" width="${x(e)-x(s)}" height="25" rx="4" fill="${color}"/><text x="${x(s)+7}" y="${y+17}" fill="#fff" font-size="11">${esc(label)}</text>`;
    const dot=(t,y,label,up=false)=>`<circle cx="${x(t)}" cy="${y}" r="5" fill="#2d6ab8"/><text x="${x(t)}" y="${y+(up?-12:22)}" text-anchor="middle" font-size="11" fill="#4c6480">${esc(label)}</text>`;
    let svg=`<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="四并发真实时间线，单位毫秒"><title>PP2四并发的模型执行与KV完成通知时间线</title><text x="18" y="24" class="svg-small">时间原点：本组最早READ；主机动作窗口，不是纯设备执行</text>`;
    for(let t=0;t<=650;t+=100)svg+=`<line x1="${x(t)}" x2="${x(t)}" y1="42" y2="310" stroke="#e5ebf4"/><text x="${x(t)}" y="332" text-anchor="middle" class="svg-small">${t}ms</text>`;
    ['先计算请求 · 前41层','先计算请求 · 后37层','同一批采样 / 结果','恢复好请求 · 完成通知','下一批 · 前41层'].forEach((label,i)=>{svg+=`<text x="18" y="${73+i*51}" class="svg-label">${label}</text>`;});
    svg+=bar(173.297,365.782,56,'#6890c9','forward主机窗口')+bar(353.344,549.654,107,'#947eb4','forward主机窗口')+bar(550,585.332,158,'#c69b4c','');
    svg+=`<rect x="${x(369.265)}" y="205" width="${x(585.332)-x(369.265)}" height="31" fill="#fff1d5"/><line x1="${x(135.308)}" x2="${x(585.332)}" y1="221" y2="221" stroke="#b6c6db" stroke-width="2"/>`;
    svg+=dot(135.308,221,'后段8份')+dot(367.043,221,'ready≈367',true)+dot(585.332,221,'计齐≈585');
    svg+=`<line x1="${x(608.336)}" x2="${x(645)}" y1="272" y2="272" stroke="#428d74" stroke-width="8" stroke-dasharray="5 4"/><text x="${x(638)}" y="296" text-anchor="end" class="svg-small">后续未按图示长度计时</text>`+dot(589.032,272,'提交589',true)+dot(608.336,272,'开始608');
    const active=events[timelineStep];
    svg+=`<line x1="${x(active.t)}" x2="${x(active.t)}" y1="42" y2="305" stroke="#245cc7" stroke-width="2" stroke-dasharray="5 4"/><text x="${x(active.t)}" y="47" text-anchor="middle" fill="#245cc7" font-size="12" font-weight="700">${num(active.t,1)}</text></svg>`;
    $('timeline-chart').innerHTML=svg;
    $('timeline-explain').innerHTML=`<span class="badge fact">${timelineStep+1} / ${events.length} · 实际观测与源码对应</span><h3>${esc(active.title)}</h3><p>${esc(active.body)}</p><p class="fine">黄色区间只是“载荷构造完成→核心计齐”的定位范围，包含尚未拆清的MQ与结果路径，不是已证明可省掉的216ms。</p>`;
    $('timeline-buttons').innerHTML=events.map((e,i)=>`<button type="button" data-time="${i}" aria-pressed="${timelineStep===i}">${i+1}. ${esc(e.short)}</button>`).join('');
    $('timeline-step').value=timelineStep;
  }
  $('timeline-step').addEventListener('input',e=>{timelineStep=Number(e.target.value);renderTimeline();});
  $('timeline-buttons').addEventListener('click',e=>{const b=e.target.closest('[data-time]');if(b){timelineStep=Number(b.dataset.time);renderTimeline();}});

  function renderRestore(){
    const requests=data.kv_restore_observations.requests;
    const select=(p,k)=>requests.find(x=>x.case===`pp${p}-${k}k-hot`);
    const rr=[select(1,4),select(2,4),select(1,8),select(2,8)];
    bars('restore-chart',rr.map(r=>({label:`${r.layout} · ${r.case.includes('8k')?'8K':'4K'}恢复`,value:r.handler_sum_ms_service_time_not_request_latency,color:r.layout==='PP1'?'#9d82ba':'#428e77'})),'16份handler服务耗时之和（毫秒）','同长度KV恢复主机服务占用比较，非请求TTFT或DMA带宽');
    $('restore-table').innerHTML='<thead><tr><th>只比较这些恢复诊断</th>'+rr.map(r=>`<th>${r.layout} · ${r.case.includes('8k')?'8K':'4K'}</th>`).join('')+'</tr></thead><tbody>'+[
      ['计划提交KV量（MiB）',r=>num(r.attempted_payload_MiB,1)],
      ['READ / 请求',r=>`${r.READ_count}份（16 rank各一份）`],
      ['native提交次数 / 请求',r=>num(r.native_plan_calls,0)],
      ['恢复对象数 / 请求',r=>num(r.parts.objects,0)],
      ['block-ID索引CPU源 / rank',r=>`${r.index_source_bytes_per_worker}字节`],
      ['16份handler主机耗时之和',r=>`${num(r.handler_sum_ms_service_time_not_request_latency,3)}ms`]
    ].map(([label,fn])=>`<tr><th>${esc(label)}</th>${rr.map(r=>`<td>${esc(fn(r))}</td>`).join('')}</tr>`).join('')+'</tbody>';
  }

  let selectedCode=sources.snippets[0].id;
  function renderCode(){
    const snippet=sources.snippets.find(x=>x.id===selectedCode);
    $('code-tabs').innerHTML=sources.snippets.map((s,i)=>`<button type="button" data-code="${esc(s.id)}" aria-pressed="${s.id===selectedCode}">${i+1}. ${esc(['查设备event','收集完成状态','后段返回None','聚合器先过闸','收齐worker响应','核心等待结果'][i])}</button>`).join('');
    const code=snippet.fragments.map((f,fi)=>{
      const highlights=new Set(snippet.key_lines.flatMap(k=>k.locations.filter(l=>l.source_path===f.source_path).map(l=>l.line)));
      const lines=f.code.replace(/\n$/,'').split('\n').map((line,i)=>`<div class="code-line ${highlights.has(f.line_start+i)?'highlight':''}"><span class="ln">${f.line_start+i}</span><span class="src">${esc(line)}</span></div>`).join('');
      return `${fi ? '<div class="code-gap">… 以下为另一个独立源码片段，未拼成可执行文件 …</div>':''}<div class="code-gap">${esc(f.source_path)} : ${f.line_start}–${f.line_end}</div>${lines}`;
    }).join('');
    $('code-content').innerHTML=`<div class="code-intro"><span class="badge source">当前安装版本的真实源码摘录</span><h3>${esc(snippet.title)}</h3><p><code>${esc(snippet.function)}</code></p><p class="fine">${esc(snippet.omission_note)}</p></div><div class="code-split"><div class="code-pane" aria-label="实际源码，附原始行号">${code}</div><div class="code-explain"><h3>先读中文</h3><ol>${snippet.plain_steps.map(x=>`<li>${esc(x)}</li>`).join('')}</ol><h4>蓝底代码行在判断什么？</h4>${snippet.key_lines.map(x=>`<p><code>${esc(x.match)}</code><br>${esc(x.explanation)}</p>`).join('')}<h4>这些代码还不能证明</h4>${list(snippet.does_not_prove)}</div></div><div class="code-proof"><b>它能证明：</b>${esc(snippet.proves.join('；'))}</div><div class="code-path">证据保存：${[...new Set(snippet.fragments.map(f=>f.source_bundle_path))].map(p=>link(p,p)).join('<br>')}</div>`;
  }
  $('code-tabs').addEventListener('click',e=>{const b=e.target.closest('[data-code]');if(b){selectedCode=b.dataset.code;renderCode();}});
  $('pseudo-code').innerHTML=sources.teaching_pseudocode.map(p=>`<article><h3>${esc(p.title)}</h3><p class="notice-unknown">${esc(p.warning)}</p><pre class="pseudo">${esc(p.code)}</pre><p class="fine">${esc(p.plain)}</p></article>`).join('');
  $('glossary-list').innerHTML=glossary.items.map(t=>`<div class="term-row"><strong>${esc(t.term)}</strong><div><p>${esc(t.meaning)}</p><p class="fine">${esc(t.example)}</p></div></div>`).join('');
  const copyTexts=new Map();
  $('baseline-commands').innerHTML=`<p class="notice-unknown">${esc(commands.comparison_boundary)}<br>${esc(commands.reproduction_boundary)}</p>`+commands.items.map((c,i)=>{
    const content=c.command ? `${c.environment_text || ''}${c.environment_text ? '\n' : ''}${c.command}` : '';
    if(content)copyTexts.set(String(i),content);
    return `<article class="command-block"><h3>${esc(c.title)}${content?`<button type="button" data-copy="${i}">复制完整环境与命令</button>`:''}</h3><p>${esc(c.layout || '')}<br>${esc(c.route || '')}</p><p>${link(c.source_path,'实际归档源文件')} · 归档日期 ${esc(c.archive_date)} · ${esc(c.readiness || '')}</p>${(c.dependencies || []).map(d=>`<p><strong>${esc(d.name)}：</strong>${esc(d.status)}。${esc(d.detail)}</p>`).join('')}${content?`<pre><code>${esc(content)}</code></pre>`:`<p>实际OFF启动脚本保留在上方链接；${link(c.native_display_reference,'只供阅读的原生OFF稿')}明确为未执行展示稿，不冒充实际验收命令。</p>`}</article>`;
  }).join('');
  $('baseline-commands').addEventListener('click',async e=>{
    const b=e.target.closest('[data-copy]');if(!b)return;
    const text=copyTexts.get(b.dataset.copy);
    try{
      if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(text);
      else{const t=document.createElement('textarea');t.value=text;t.style.position='fixed';t.style.left='-9999px';document.body.appendChild(t);t.select();const ok=document.execCommand('copy');t.remove();if(!ok)throw new Error('clipboard unavailable');}
      b.textContent='已复制';$('copy-status').textContent='已复制完整环境与归档命令；没有执行。';
    }catch{b.textContent='请选中下方命令复制';$('copy-status').textContent='浏览器未开放剪贴板，请在下方代码块中手动复制。';}
  });
  const navLinks=[...document.querySelectorAll('.nav>a')];
  if('IntersectionObserver' in window){const observer=new IntersectionObserver(entries=>{const top=entries.filter(e=>e.isIntersecting).sort((a,b)=>a.boundingClientRect.top-b.boundingClientRect.top)[0];if(top)navLinks.forEach(a=>a.classList.toggle('current',a.hash==='#'+top.target.id));},{rootMargin:'-80px 0px -65% 0px'});document.querySelectorAll('main>section').forEach(s=>observer.observe(s));}
  function actionBudget(){return `<h3>另看两个全命中请求的P侧动作收支</h3><p>以下每形态仅2个历史全命中请求，统计主机动作窗口，不是纯NPU算时或整档TTFT。</p><div class="table-wrap"><table><thead><tr><th>指标</th><th>PP2</th><th>PP1</th></tr></thead><tbody>${[
    ['OFF计算批窗口（ms）','off_compute_batch_ms'],['ON计算批窗口（ms）','on_compute_batch_ms'],['计算批窗口减少比例（%）','saved_percent'],['ON新增恢复与推进（ms）','restore_and_advance_ms'],['P窗口总变化：正为变慢（ms）','p_window_delta_ms']
  ].map(([label,key])=>`<tr><th>${label}</th>${data.historical_two_sample_action_budget.map(x=>`<td>${num(x[key],key==='saved_percent'?2:3)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;}
  renderFlow();renderResults();renderSfa();renderTimeline();renderRestore();renderCode();
  ['tier-select','metric-select'].forEach(id=>$(id).addEventListener('change',renderResults));
  let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{renderResults();renderSfa();renderRestore();},120);});
})();
