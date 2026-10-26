"""用已归档时间戳画纵向时间图；只生成本地HTML，不访问实验服务器。"""
from html import escape
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT / "05_当前环境与实验准备/20261023_夜间验证/分析/PP2_通知链/pp2-on/strict_analysis.json"
data = json.loads(SOURCE.read_text(encoding="utf-8"))
case = data["c4"][0]
front = next(r for r in case["ranks"] if r["rank"] == 0)
carrier = data["positive_carrier_commands"]["3402"]
front_body = next(s for s in carrier["model_body_spans"] if s["rank"] == 0)
back_body = next(s for s in carrier["model_body_spans"] if s["rank"] == 8)
front_return = next(s for s in carrier["worker_end_events"] if s["rank"] == 0)
back_events = {e["event"]: e for e in carrier["sample_events"] if e["rank"] == 8}
origin = front_body["start_ns"]


def ms(stamp):
    return (stamp - origin) / 1_000_000


def stamp(event):
    return ms(event["time_monotonic_ns"])


ready = stamp(front["ready_query_event"])
report = stamp(front["worker_report_event"])
count = stamp(front["core_rank_count_event"])
aggregate = stamp(case["common_events"]["kv.aggregate_finished_recving"])
promote = stamp(case["common_events"]["kv.scheduler_promote_ready"])
submit = stamp(case["actual_positive_executor"])
wait = count - report
assert carrier["model_request_ids"] != [case["request_id"]]
assert 210 < wait < 215

# 纵坐标严格按时间比例。说明文字移开时，用引线指回真实时间点。
parts = []


def add(value):
    parts.append(value)


def y(time):
    return 122 + time * 2.5


def text(x, ypos, lines, css="label", anchor="start"):
    for i, line in enumerate(lines):
        add(f'<text class="{css}" x="{x}" y="{ypos+i*25}" text-anchor="{anchor}">{escape(line)}</text>')


def block(x, begin, end, css, title):
    add(f'<rect class="{css}" x="{x}" y="{y(begin):.3f}" width="250" height="{y(end)-y(begin):.3f}" rx="6"><title>{escape(title)}</title></rect>')


def line(x1, yy1, x2, yy2, css="handoff", arrow=False):
    add(f'<path class="{css}" d="M{x1},{yy1:.3f} L{x2},{yy2:.3f}"'+(' marker-end="url(#arrow)"' if arrow else '')+'/>')


def dot(x, at, title):
    add(f'<circle class="event" cx="{x}" cy="{y(at):.3f}" r="5"><title>{escape(title)}</title></circle>')


add('<svg id="vertical-timeline" role="img" aria-labelledby="chart-title chart-desc" viewBox="0 0 1240 1300" xmlns="http://www.w3.org/2000/svg">')
add('<title id="chart-title">时间向下、worker横排：一条真实记录中的计算与通知等待</title>')
add('<desc id="chart-desc">前段从0秒到0.177秒执行正在计算的请求；约0.179秒报告另一条缓存命中请求的KV完成。后段约0.186至0.351秒执行正在计算的请求，再处理采样及输出。核心约0.391秒才计数前段报告，缓存命中请求约0.396秒提交剩余计算。</desc>')
add('<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10Z" fill="#476681"/></marker></defs>')
for x in (110, 390, 670, 950):
    add(f'<rect x="{x}" y="103" width="250" height="1080" class="lane"/>')
text(22, 36, ["时间 ↓"], "head")
text(22, 64, ["单位：秒", "本段起点=0"], "small")
for x, title, subtitle in ((235, "前段 worker", "代表：rank0，前41层"), (515, "后段 worker", "代表：rank8，后37层"), (795, "完成报告的返回通路", "执行器收集模型批结果"), (1075, "调度核心", "看缓存命中请求的状态")):
    text(x, 36, [title], "head", "middle")
    text(x, 66, [subtitle], "small", "middle")
for tick in range(0, 421, 50):
    pos = y(tick)
    line(92, pos, 1210, pos, "grid")
    text(80, pos+5, [f"{tick/1000:.3f}"], "tick", "end")

block(110, 0, ms(front_body["end_ns"]), "compute", "前段模型方法窗口；含CPU调用、设备工作与可能的同步，不是纯算子计时")
text(235, 224, ["正在计算的请求", "执行前41层", "0.000—0.177秒"], "block-label", "middle")
text(235, 340, ["这是模型方法记录的区间", "不是纯设备计算的计时"], "small", "middle")
dot(350, ready, "前段代表worker查询到设备KV恢复已完成")
dot(350, report, "前段报告缓存命中请求KV完成")
dot(350, stamp(front_return), "前段execute_model方法返回；不等于整批设备工作完成")
line(350, y(report), 670, y(report), "signal", True)
text(125, y(report)+35, ["约0.178秒：前段KV就绪", "约0.179秒：前段发出完成报告", "约0.180秒：前段方法返回"], "green-text")
text(125, y(report)+140, ["报告属于：", "缓存命中的请求", "不是正在计算的请求"], "label")
text(125, y(report)+285, ["这之后前段的其他活动", "本图没有逐项展开。", "空白不代表前段空闲。"], "small")

# 虚线只表达中间结果的逻辑交接，不冒充send/recv精确时间戳。
line(360, y(ms(front_body["end_ns"])), 390, y(ms(back_body["start_ns"])), "logical", True)
text(515, 210, ["正在计算的请求", "将从前段继续到后段", "两段之间交接中间计算结果", "（hidden state）"], "label", "middle")
text(515, 365, ["这段前置活动未逐项计时", "不能全部算成PP通信等待"], "small", "middle")
block(390, ms(back_body["start_ns"]), ms(back_body["end_ns"]), "compute", "后段模型方法窗口：185.528—351.068ms")
text(515, y(232), ["正在计算的请求", "执行后37层", "0.186—0.351秒"], "block-label", "middle")
text(515, y(298), ["这里算的是另一条请求。", "此时并非还在恢复", "缓存命中请求的后段KV。"], "small", "middle")
block(390, stamp(back_events["sample_detail.total.begin"]), stamp(back_events["sample_detail.total.end"]), "sample", "后段采样方法窗口，包含MTP草拟；不是纯采样算子时间")
text(515, y(362), ["采样、MTP等：约30ms"], "compact", "middle")
block(390, stamp(back_events["sample_detail.output_event_wait.begin"]), stamp(back_events["sample_detail.output_event_wait.end"]), "device-wait", "输出事件等待约4ms；方法返回与设备结果可交付不是同一时刻")
line(640, y(stamp(back_events["sample_detail.output_event_wait.end"])), 670, y(count), "handoff", True)
text(405, y(407), ["约0.389秒：输出等待结束", "随后返回结果，核心继续处理"], "small")

block(670, report, count, "notification-wait", "前段报告产生到核心计数：212.241ms；不是KV搬运时间")
text(795, y(report)+48, ["前段报告已经产生"], "amber-text", "middle")
text(795, y(235), ["缓存命中的请求：", "KV完成报告", "尚未在核心计数"], "block-label", "middle")
text(795, y(290), [f"这个窗口约 {wait:.1f}ms", "后段的模型、采样和输出", "仍在推进同一批返回通路"], "label", "middle")
text(795, y(340), ["不是KV又搬了212ms", "也不是执行器CPU一直空等"], "small", "middle")
line(640, y(263), 665, y(263), "dependency", True)
text(795, y(414), ["约0.391秒：核心计数前段报告"], "green-text", "middle")

block(950, ready, aggregate, "request-wait", "缓存命中请求仍未获准继续计算；不表示核心没有处理其他事务")
text(1075, y(225), ["缓存命中的请求", "还未获准继续计算"], "block-label", "middle")
text(1075, y(280), ["这一列画请求状态。", "核心可能仍处理其他事务，", "不是整个核心停了212ms。"], "small", "middle")
text(1075, y(335), ["必须按请求ID收齐", "所需分片的完成报告，", "再满足调度和资源条件。"], "label", "middle")
line(920, y(count), 950, y(count), "signal", True)
dot(965, aggregate, "核心汇齐16个rank的完成报告")
dot(1075, promote, "调度器使缓存命中请求可以恢复计算")
dot(1185, submit, "向执行器提交缓存命中请求的剩余计算；该批共有3条请求")
text(960, y(405), ["0.392秒：汇齐完成报告", "0.394秒：获准继续计算", "0.396秒：提交剩余token"], "green-text")
text(110, 1260, ["同一条水平线 = 同一时刻。后段在计算时，另一请求的完成报告可以同时处于等待状态。"], "caption")
add('</svg>')

events = [
    ("前段模型方法开始", front_body["start_ns"], front_body),
    ("前段模型方法结束", front_body["end_ns"], front_body),
    ("前段查询到KV已恢复", front["ready_query_event"]["time_monotonic_ns"], front["ready_query_event"]),
    ("前段报告KV完成", front["worker_report_event"]["time_monotonic_ns"], front["worker_report_event"]),
    ("前段worker方法返回", front_return["time_monotonic_ns"], front_return),
    ("后段模型方法开始", back_body["start_ns"], back_body),
    ("后段模型方法结束", back_body["end_ns"], back_body),
    ("后段采样方法结束", back_events["sample_detail.total.end"]["time_monotonic_ns"], back_events["sample_detail.total.end"]),
    ("后段输出事件等待结束", back_events["sample_detail.output_event_wait.end"]["time_monotonic_ns"], back_events["sample_detail.output_event_wait.end"]),
    ("核心计数前段完成报告", front["core_rank_count_event"]["time_monotonic_ns"], front["core_rank_count_event"]),
    ("核心汇齐全部完成报告", case["common_events"]["kv.aggregate_finished_recving"]["time_monotonic_ns"], case["common_events"]["kv.aggregate_finished_recving"]),
    ("获准继续计算", case["common_events"]["kv.scheduler_promote_ready"]["time_monotonic_ns"], case["common_events"]["kv.scheduler_promote_ready"]),
    ("提交剩余计算", case["actual_positive_executor"]["time_monotonic_ns"], case["actual_positive_executor"]),
]
rows = "".join(f'<tr><td>{escape(name)}</td><td>{ms(ns)/1000:.6f}</td><td>{escape(Path(e["source_file"]).name)}:{e["source_line"]}</td></tr>' for name, ns, e in events)
page = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>纵向时间轴：前后段worker谁在算、谁在等</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f5f6f9;color:#24354b;font:16px/1.8 "Microsoft YaHei",sans-serif}main{max-width:1350px;margin:auto;padding:28px 22px 50px}h1{font-size:28px;line-height:1.5;margin:0 0 10px}p{margin:10px 0}.lead{font-size:18px;max-width:1100px}.intro{background:#fff;border-left:4px solid #3a6b9e;padding:14px 20px;margin:20px 0}.legend{display:flex;flex-wrap:wrap;gap:12px 25px;font-size:14px;margin:15px 0}.key{display:inline-block;width:16px;height:12px;margin-right:7px;vertical-align:middle;border:1px solid #8193a7}.chart{background:#fff;border:1px solid #d9e0e9;border-radius:8px;overflow:auto}svg{display:block;width:100%;min-width:1060px;height:auto}.lane{fill:#fafbfd;stroke:#e5eaf0}.grid{stroke:#e6ebf1;stroke-width:1;fill:none}.head{font-size:19px;fill:#253c56;font-weight:700}.small{font-size:15px;fill:#596d84}.tick{font:15px Consolas,monospace;fill:#4e637d}.label,.block-label{font-size:17px;fill:#283b51}.block-label{font-weight:700}.compact{font-size:15px;fill:#303d65}.caption{font-size:17px;fill:#354d69}.compute{fill:#dceafb;stroke:#5c8fc7}.sample{fill:#e7e1f8;stroke:#9b88bc}.device-wait{fill:#e7d6bf;stroke:#ac7b45}.notification-wait{fill:#ffebbf;stroke:#d6a448}.request-wait{fill:#fff4da;stroke:#dbbd7a}.event{fill:#167350;stroke:white;stroke-width:1}.green-text{font-size:15px;fill:#126648;font-weight:700}.amber-text{font-size:17px;fill:#8b5711;font-weight:700}.signal{stroke:#258765;stroke-width:2;stroke-dasharray:4 2;fill:none}.handoff{stroke:#476681;stroke-width:2;fill:none}.logical{stroke:#476681;stroke-width:2;stroke-dasharray:4 3;fill:none}.dependency{stroke:#b17c2d;stroke-width:2;fill:none}.takeaway{background:#fff4da;padding:16px 20px;border-radius:6px;font-size:17px;margin:18px 0}details{background:#fff;padding:14px 18px;border:1px solid #dfe5ed;margin-top:17px}summary{cursor:pointer;font-weight:700}table{border-collapse:collapse;width:100%;font-size:14px;margin:14px 0}td,th{text-align:left;padding:8px 10px;border:1px solid #e1e6ed}th{background:#f2f5f9}code{overflow-wrap:anywhere;font-size:13px}a{color:#28669d}.boundary{font-size:14px;color:#52647b}footer{margin-top:18px;font-size:14px;color:#61748a}@media(max-width:700px){main{padding:20px 12px}h1{font-size:23px}.lead{font-size:16px}}
</style></head><body><main>
<h1>同一时刻，哪个worker在算，哪条请求在等？</h1>
<p class="lead"><b>纵轴是时间，越往下越晚；横轴是前段worker、后段worker、结果返回通路和调度核心。</b>色块有多高，就表示这个动作或状态持续了多久。沿同一条水平线横着看，就能看到并行发生的事情。</p>
<div class="intro"><b>固定看这两条请求：</b><br>
<b>正在计算的请求：</b>本图中先经过前41层，再经过后37层；不据此判断它是否缓存未命中。<br>
<b>缓存命中的请求：</b>后段KV在本图开始前已经恢复并报告；前段稍后完成。它在等待恢复完成消息被核心处理，还没有开始剩余token的计算。</div>
<div class="legend"><span><i class="key" style="background:#dceafb"></i>模型方法执行区间</span><span><i class="key" style="background:#e7e1f8"></i>采样／MTP方法区间</span><span><i class="key" style="background:#ffebbf"></i>报告或请求仍在等待</span><span><i class="key" style="background:#167350"></i>完成／状态变化</span></div>
<p class="boundary">下图来自10/23归档的一条PP2并发记录，不是虚构的秒数，也不是全部请求的平均情况。前后段各画一个代表worker；方法区间包含CPU调用、设备执行或同步，不能当成纯算子耗时。</p>
<div class="chart">__CHART__</div>
<div class="takeaway"><b>这张图想说明：</b>前段已经报告“缓存命中的请求”的KV完成；后段仍在处理“正在计算的请求”的模型、采样和输出。当前实现把完成报告带在模型批的返回通路里，所以核心较晚才计数这条报告。<b>等的是报告被处理，不是KV继续搬运。</b></div>
<p>报告处理完成之后，缓存命中的请求才有机会进入后续计算。剩余token仍要经过前41层和后37层；本图止于<b>提交这次计算</b>，没有把尚未展示的后续执行画成实测。它可以与其他请求同批，这次新提交的批实际有3条请求。</p>
<details><summary>展开精确时间和日志出处</summary>
<p>所有时间来自同一台P服务器的单调时钟；以rank0本次模型方法开始作为0秒。图中约数仅为便于阅读。</p>
<table><thead><tr><th>观测动作</th><th>相对时间／秒</th><th>日志文件：行号</th></tr></thead><tbody>__ROWS__</tbody></table>
<p>缓存命中的请求ID：<code>__HIT_ID__</code><br>正在计算的请求ID：<code>__COMPUTE_ID__</code></p>
<p>前段报告属于前一条请求，但携带它的模型命令3402只计算后一条请求；命令与核心等待的结果对象已按ID对应，不是仅靠时间接近推断。</p>
<p><a href="__SOURCE__">结构化分析原件</a> · <a href="__CONCLUSION__">该条记录的说明</a></p>
</details>
<details><summary>这张图不能推出什么？</summary>
<p>① 前段其他7个worker比rank0稍晚就绪，不能把rank0的时刻当成前段全部完成。② 后段空白不是已测得的纯通信等待，前段空白也不是CPU空闲。虚线只示意中间结果的逻辑交接，没有精确标出发送／接收时刻。</p>
<p>③ 约212ms是本例“报告产生→核心计数”的窗口，不是PP2相对PP1新增的时间，也不是全部能删除的时间。PP1同样可能有按批返回和按序处理；本图展示的是PP2下已观察到的一条长等待记录。</p>
<p>④ 该轮C4正式32条请求中，“全部分片就绪→提交后续计算”的中位数约3.95ms，不能用这条长等待代表全部请求。⑤ 提前通知还不等于能立即计算，仍需真实KV全部就绪、计算名额可用、内存及数据依赖满足，并保持模型结果处理顺序。</p>
</details>
<footer>本页重新绘成纵向等比例时间图；没有改动历史原图、推理源码或任何服务器服务。小屏可在图内左右滑动。</footer>
</main><script>
addEventListener('load',()=>{
 const svg=document.getElementById('vertical-timeline');
 const labels=[...svg.querySelectorAll('text')].map(e=>({text:e.textContent,b:e.getBBox()}));
 const overlap=[];for(let i=0;i<labels.length;i++)for(let j=i+1;j<labels.length;j++){let a=labels[i].b,b=labels[j].b;if(Math.min(a.x+a.width,b.x+b.width)>Math.max(a.x,b.x)+1&&Math.min(a.y+a.height,b.y+b.height)>Math.max(a.y,b.y)+1)overlap.push([labels[i].text,labels[j].text]);}
 const result={title:document.title,svgCount:document.querySelectorAll('svg').length,textCount:labels.length,overflow:labels.filter(e=>e.b.x<0||e.b.x+e.b.width>1240||e.b.y<0||e.b.y+e.b.height>1300).map(e=>e.text),overlap,pageOverflow:document.documentElement.scrollWidth>innerWidth,externalRequests:[...document.querySelectorAll('script[src],img[src],link[href]')].length};
 const out=document.createElement('script');out.type='application/json';out.id='render-check';out.textContent=JSON.stringify(result);document.body.appendChild(out);document.documentElement.dataset.rendered='true';
});
</script></body></html>'''
page = (page.replace("__CHART__", "".join(parts)).replace("__ROWS__", rows)
        .replace("__HIT_ID__", escape(case["request_id"]))
        .replace("__COMPUTE_ID__", escape(carrier["model_request_ids"][0]))
        .replace("__SOURCE__", SOURCE.as_uri())
        .replace("__CONCLUSION__", SOURCE.with_name("本轮结论.md").as_uri()))
assert "\ufffd" not in page and "__CHART__" not in page
target = HERE / "pp_lmcache_one_diagram.html"
target.write_text(page, encoding="utf-8")
assert not (target.stat().st_file_attributes & 2), "入口必须是普通可见文件"
print(f"VISIBLE_HTML: {target}")
print(f"PASS: 纵轴时间、横轴worker；真实报告等待 {wait:.3f}ms；无外部图片。")
