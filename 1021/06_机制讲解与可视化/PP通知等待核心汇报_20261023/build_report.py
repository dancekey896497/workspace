"""Build a self-contained readout from existing evidence. No server operations."""
from pathlib import Path
from html import escape
import base64
import json

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent
EVIDENCE = OUTPUT / 'ks_rootcause_window_20261021'
VISUAL = Path('C:/Users/luv_l/.codex/visualizations/2026/10/22/01a16451-9c7a-7f60-877a-1c271d8a90fe')

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def href(path, label):
    return '<a href="' + Path(path).resolve().as_uri() + '">' + escape(label) + '</a>'

def main():
    accounting_path = OUTPUT / 'ks_cpu_causality_20261021/accounting/paired-P-action-accounting.json'
    paired_path = OUTPUT / 'ks_test_results_structured_20261021/data/paired-performance-analysis.json'
    notify_dir = EVIDENCE / 'analysis/continuation_1730/notification'
    query_path = notify_dir / 'c4-ready-query-chain.json'
    worker_path = notify_dir / 'c4-worker-output-boundary.json'
    critical_path = EVIDENCE / 'analysis/pp2/c4-critical-path.json'
    focus_path = EVIDENCE / 'analysis/pp2/c4-core-focus-complete.json'
    accounting = read(accounting_path)['rows'][0]
    paired = read(paired_path)
    query_data = read(query_path)
    request = query_data['requests'][1]
    worker = next(r for r in request['ranks'] if r['rank'] == 0)
    carrier = next(r for r in read(worker_path)['ranks'] if r['rank'] == 0)['actual_worker_end_event']
    critical = next(r for r in read(critical_path)['requests'] if r['case'] == request['case'])
    focus = read(focus_path)['events']
    origin = query_data['time_origin_wall_ns']
    def rel(e): return e.get('relative_ms', (e['time_wall_ns']-origin)/1e6)
    def point(name):
        return max((e for e in critical['points'] if e['event'] == name), key=lambda e:e['time_monotonic_ns'])
    execute_end = next(e for e in focus if e['event']=='core.worker_response_wait.end' and e.get('source_line')==38948)
    sample_end = next(e for e in focus if e['event']=='core.worker_response_wait.end' and e.get('source_line')==38985)
    all_finished = point('kv.aggregate_finished_recving')
    scheduler_finished = point('kv.scheduler_finished_recving')
    submit = point('pipeline.executor_submit')
    enqueue = point('pipeline.command_enqueue')
    events = [worker['query_event'],worker['worker_report_event'],carrier,execute_end,
              worker['core_count_event'],all_finished,sample_end,scheduler_finished,submit,enqueue]
    event_names = ['前段worker查到设备KV已就绪','同一个前段worker报告KV恢复完成',
                   '这个前段worker交出当前批返回信息','本次模型执行的worker回复收齐',
                   '核心结果处理链计入这个worker的完成消息','缓存命中请求所需16份确认齐全',
                   '本批后续结果的完整等待返回','调度器实际收到KV完成信息',
                   '核心将缓存命中请求交给执行器','新计算指令进入派发队列']
    raw_excerpt = []
    for label,e in zip(event_names,events):
        fields = {k:e[k] for k in ['event','request_id','rank','command_id','ready',
                  'raw_response_processed','expected_finished_count','remaining_finished_count',
                  'time_monotonic_ns','source_line'] if k in e}
        raw_excerpt.append({'说明':label,'相对时间_ms':rel(e),'归档中的原始字段':fields})
    tiers = [g['tiers'][1] for g in paired['groups'][:2]]
    ttft = []
    for label,tier in zip(['PP2：P在12','PP1：P在8'],tiers):
        metric = next(m for m in tier['metrics'] if m['key']=='ttft_s')
        ttft.append({'label':label,'n':tier['n'],'off_success':tier['off_full_run_success'],
                     'on_success':tier['on_full_run_success'],'attempts':tier['attempted_per_side'],
                     'off_ms':metric['off_mean']*1000,'on_ms':metric['on_mean']*1000,
                     'relative_percent':metric['relative_percent'],'metric_sources':metric['sources']})
    assert accounting['n']==2 and all(t['n']==27 for t in ttft)
    assert abs(accounting['on_compute_ms']+accounting['restore_and_progress_ms']-accounting['on_P_window_ms'])<0.001
    states = [
        {'label':'KV好了','time':worker['ready_query_relative_ms'],
         'worker':'前段worker查到：缓存命中的请求，其设备KV已经就绪。',
         'executor':'这条完成消息还没有进入核心的完成计数。',
         'scheduler':'仍然记录为等待外部KV，尚不能据此安排它计算。'},
        {'label':'worker交出消息','time':rel(carrier),
         'worker':'前段worker已把这条完成消息放进正在计算的请求所属批次的返回信息，并结束方法。',
         'executor':'返回信息何时进入可读队列尚未采齐；整批其他worker的回复还在推进。',
         'scheduler':'仍未处理到这条完成消息。方法返回也不等于前段设备完全空闲。'},
        {'label':'本次回复收齐','time':rel(execute_end),
         'worker':'前段的KV完成信息已经准备好；后段还没有交付这一批的最终模型输出。',
         'executor':'本次执行回复已收齐。按原规则，因为负责最终输出的worker此时未交付模型结果，暂不整理前段KV消息。（指定输出为None。）',
         'scheduler':'核心仍沿模型结果链等待，尚未解除缓存命中请求的KV等待。'},
        {'label':'核心开始计入','time':worker['core_count_relative_ms'],
         'worker':'前段的同一份完成信息沿后续输出回复被处理；这不是再次恢复KV。',
         'executor':'在核心进程的结果处理链中，计入前段worker 0的标记；此刻还剩7份确认。',
         'scheduler':'这一步是完成计数，不是调度器已处理齐所有通知。'},
        {'label':'16份确认齐全','time':rel(all_finished),
         'worker':'这个请求所需的16份完成确认已经收齐。',
         'executor':'完成消息整理齐全；本批后续结果的完整等待在约585.560ms结束。',
         'scheduler':'还需把完成信息交给调度器更新，不应把计数时刻当成设备开算。'},
        {'label':'调度器更新','time':rel(scheduler_finished),
         'worker':'KV已经就绪，等待后续计算安排。',
         'executor':'相应完成信息已经交给调度器处理。',
         'scheduler':'记录缓存命中请求已收到外部KV完成确认，后续调度可检查是否安排剩余token。'},
        {'label':'下一批提交','time':rel(submit),
         'worker':'即将接收包含缓存命中请求的新计算安排；剩余token仍需经过前41层和后37层。',
         'executor':'核心已提交后续计算批；约589.724ms指令进入派发队列。',
         'scheduler':'这是提交时间，不是实际设备开始计算的时间。'}]
    data = {'date':'2026-10-23','collection_dates':['2026-10-19','2026-10-20','2026-10-21'],
            'accounting':accounting,'ttft':ttft,'timeline_states':states,
            'same_worker_wait_ms':worker['query_to_core_count_ms_same_host_monotonic'],
            'query_to_report_ms':worker['query_to_worker_report_ms_same_thread_monotonic'],
            'worker_report_to_count_ms':worker['worker_report_to_core_count_ms_same_host_monotonic'],
            'raw_event_excerpt':raw_excerpt,'source_files':[str(p) for p in [accounting_path,paired_path,query_path,worker_path,critical_path,focus_path]],
            'limits':['Different samples: C1 action accounting is not the C4 notification trace.',
                      'Cross-layout TP, CP, date, P host and observation conditions differ.',
                      'Archived structured events retain source lines; not verbatim full original trace.',
                      'Observed notification latency is not a measured removable latency or PP1-relative increment.'],
            'new_requests_sent':0,'NPU_used':False,'runtime_source_changed':False,'services_changed':False,'KS_MEM_001':'OPEN'}
    (ROOT/'report-data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    rows=''.join('<tr><td>'+escape(label)+'</td><td>'+f'{rel(e):.3f}'+'</td><td><code>'+escape(e['event'])+'</code></td><td>'+str(e.get('source_line',''))+'</td></tr>' for label,e in zip(event_names,events))
    perf_rows=''.join(f'<tr><th>{escape(t["label"])}</th><td>{t["off_ms"]:.3f}</td><td>{t["on_ms"]:.3f}</td><td class="good">↓{abs(t["relative_percent"]):.2f}%</td><td>{t["n"]}对</td></tr>' for t in ttft)
    chart=''
    for t in ttft:
        chart+=f'<div class="bar-pair"><b>{escape(t["label"])}</b><div class="barline"><span>OFF</span><div class="track"><i class="off" style="width:{t["off_ms"]/2300*100:.4f}%">{t["off_ms"]:.0f}ms</i></div></div><div class="barline"><span>ON</span><div class="track"><i class="on" style="width:{t["on_ms"]/2300*100:.4f}%">{t["on_ms"]:.0f}ms</i></div></div></div>'
    links=[]
    for p,label in [(accounting_path,'两条完整命中请求的P窗口收支'),(paired_path,'正式同输入配对结果与原始结果路径'),
                    (query_path,'同请求同worker的设备就绪、报告与核心计数'),(critical_path,'四并发请求关键事件'),
                    (focus_path,'核心等待执行／后续输出结果的事件'),
                    (OUTPUT/'ks_pp_lmcache_explained_20261021/source_code_notes.md','worker与执行器的原源码说明')]:
        links.append('<li>'+href(p,label)+'</li>')
    raw='<pre>'+escape(json.dumps(raw_excerpt,ensure_ascii=False,indent=2))+'</pre>'
    full_png=OUTPUT/'pp_lmcache_sequence.png'
    image='data:image/png;base64,'+base64.b64encode(full_png.read_bytes()).decode('ascii')
    replacements={'@@DATA_JSON@@':json.dumps(data,ensure_ascii=False).replace('<','\\u003c'),
                  '@@LOG_ROWS@@':rows,'@@RAW_EVENTS@@':raw,'@@PERF_ROWS@@':perf_rows,
                  '@@PERF_CHART@@':chart,'@@SOURCE_LINKS@@':'\n'.join(links),'@@FULL_SEQUENCE@@':image}
    html=(ROOT/'report.template.html').read_text(encoding='utf-8')
    for key,value in replacements.items():html=html.replace(key,value)
    assert '@@' not in html and '\ufffd' not in html and '???' not in html
    target=ROOT/'pp_lmcache_core_report.html'
    target.write_text(html,encoding='utf-8')
    visual=VISUAL/'pp_lmcache_core_report.html'
    visual.write_text(html,encoding='utf-8')
    print(json.dumps({'status':'REPORT_BUILT','workspace':str(target),'viewer':str(visual),
                      'same_worker_wait_ms':data['same_worker_wait_ms'],'data_groups':2,
                      'NPU_used':False,'runtime_source_changed':False},ensure_ascii=False))

if __name__=='__main__':main()
