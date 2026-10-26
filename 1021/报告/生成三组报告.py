#!/usr/bin/env python3
"""只读本地正式对照结果及复现资料，生成无外部依赖的中文HTML。"""
import argparse
from datetime import datetime
import html
import json
import math
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
PREP = HERE.parent
CASES = (("off", "不接LMCache"), ("async_on", "原异步ON"),
         ("optimized_on", "同步＋提前读取ON"))
COLORS = ("#5374aa", "#bb8539", "#31806c")
CORE = (("mean_ttft_ms", "平均TTFT", "秒", .001),
        ("mean_tpot_ms", "平均TPOT", "毫秒", 1),
        ("mean_e2el_ms", "平均端到端耗时", "秒", .001))


def escape(value):
    return html.escape(str(value), quote=True)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def display(value, scale=1, digits=4):
    if not number(value):
        return "待测"
    scaled = value * scale
    return f"{scaled:,}" if type(scaled) is int else f"{scaled:,.{digits}f}"


def reduction(reference, candidate):
    return (reference - candidate) / reference * 100 if number(reference) and number(candidate) and reference else None


def percentage(value):
    if value is None:
        return '<span class="muted">待测</span>'
    css = "good" if value > 0 else "bad" if value < 0 else "neutral"
    return f'<span class="{css}">{value:+.2f}%</span>'


def table(headers, rows, first_class=""):
    head = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body = "".join("<tr>" + "".join(f'<{("th" if i == 0 else "td")}>{value}</{("th" if i == 0 else "td")}>'
                               for i, value in enumerate(row)) + "</tr>" for row in rows)
    return f'<div class="table-wrap {first_class}"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def highlight(code):
    # Only syntax colors: no source rewriting, line wrapping or external highlighter.
    pattern = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"|--[\w-]+|\b(?:export|unset|source|cd|vllm|lmcache)\b|\b\d+(?:\.\d+)?\b", re.S)
    pieces = []; end = 0
    for match in pattern.finditer(code):
        pieces.append(escape(code[end:match.start()]))
        token = match.group()
        kind = "string" if token[:1] in ("'", '"') else "flag" if token.startswith("--") else "number" if token[0].isdigit() else "keyword"
        pieces.append(f'<span class="syn-{kind}">{escape(token)}</span>')
        end = match.end()
    pieces.append(escape(code[end:]))
    return '<pre><code>' + "".join(pieces) + '</code></pre>'


def command_text(path):
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")).strip()


def benchmark_for_source(command, source):
    archived = str(source).replace("\\", "/")
    if "/stages/" not in archived:
        return command
    stage_file = archived.rsplit("/stages/", 1)[1]
    result_dir = "/opt/ks-customer-bench-20261024/stages/" + stage_file.rsplit("/", 1)[0]
    return re.sub(r"(--result-dir\s+)\S+", lambda match: match.group(1) + result_dir, command)


def repro_section(cases):
    root = PREP / "实际验证配置"
    parts = ['<p>以下为完整原生服务／benchmark参数及环境。它们是复现资料；打开报告不会启动任务，也不要求重启常驻D与缓存。</p>']
    groups = [("P · " + label, "p", key) for key, label in (("off", "不接LMCache"), ("on-async", "原异步ON"), ("on-sync-prefetch", "同步＋提前读取ON"))]
    groups += [(f"共用D · rank {rank}（64K／4.5 GiB）", "d", f"d{rank}") for rank in range(4)]
    groups += [("共用LMCache · 12 GiB", "cache", "server.shared")]
    for label, folder, name in groups:
        env = root / folder / (name + ".env.native.sh")
        serve = root / folder / (name + (".native.sh" if folder == "cache" else ".serve.native.sh"))
        if env.is_file() and serve.is_file():
            body = '<p class="path">来源：' + escape(str(env)) + '<br>' + escape(str(serve)) + '</p>'
            body += '<h4>环境变量与工作目录</h4>' + highlight(command_text(env))
            body += '<h4>原生命令</h4>' + highlight('source /opt/ks-customer-bench-20261024/native-drafts/' + folder + '/' + env.name + '\n' + command_text(serve))
            parts.append('<details><summary>' + escape(label) + '</summary>' + body + '</details>')
    manual = root / "最简手工复现.md"
    if manual.is_file():
        blocks = re.findall(r"```bash\n(.*?)```", manual.read_text(encoding="utf-8"), re.S)
        benches = [block.strip() for block in blocks if "vllm bench serve" in block]
        for (key, label), command in zip(CASES, benches):
            if key in cases:
                command = benchmark_for_source(command, cases[key].get("source", ""))
            parts.append('<details><summary>正式benchmark · ' + escape(label) + '</summary>' + highlight(command) + '</details>')
    return "".join(parts)


def metric_rows():
    rows = [("正式请求数", "num_prompts", 1), ("成功请求数", "completed", 1),
            ("失败请求数", "failed", 1), ("指定并发C", "max_concurrency", 1),
            ("正式持续时间（秒）", "duration", 1), ("输入总token", "total_input_tokens", 1),
            ("平均输入token", "mean_input_tokens", 1), ("输出总token", "total_output_tokens", 1),
            ("平均输出token", "mean_output_tokens", 1)]
    for family, label, scale in (("ttft", "TTFT（秒）", .001), ("tpot", "TPOT（毫秒）", 1),
                                 ("e2el", "端到端耗时（秒）", .001), ("itl", "流块ITL（毫秒）", 1)):
        rows += [(prefix_label + label, prefix + "_" + family + "_ms", scale)
                 for prefix, prefix_label in (("mean", "平均"), ("median", "中位"), ("p95", "P95"), ("p99", "P99"))]
    rows += [("请求吞吐（req/s）", "request_throughput", 1), ("输出吞吐（token/s）", "output_throughput", 1),
             ("输入＋输出吞吐（token/s）", "total_token_throughput", 1),
             ("秒桶覆盖请求峰值（非配置并发）", "max_concurrent_requests", 1)]
    return rows


def chart(field, title, unit, scale, cases):
    values = [cases.get(key, {}).get("official", {}).get(field) for key, _ in CASES]
    maximum = max((value * scale for value in values if number(value)), default=1) or 1
    maximum *= 1.18
    width = 650; x = 215; parts = []
    for tick in range(5):
        xx = x + width * tick / 4
        parts.append(f'<line x1="{xx}" y1="10" x2="{xx}" y2="174" class="gridline"/>')
        parts.append(f'<text x="{xx}" y="197" class="axis" text-anchor="middle">{maximum * tick / 4:.2f}</text>')
    for i, ((key, label), value) in enumerate(zip(CASES, values)):
        yy = 23 + i * 55
        parts.append(f'<text x="0" y="{yy + 22}" class="bar-label">{escape(label)}</text>')
        if number(value):
            bar_width = max(2, value * scale / maximum * width)
            parts.append(f'<rect x="{x}" y="{yy}" width="{bar_width}" height="31" rx="3" fill="{COLORS[i]}"/>')
            parts.append(f'<text x="{x + bar_width + 9}" y="{yy + 22}" class="bar-value">{value * scale:.4f}</text>')
        else:
            parts.append(f'<rect x="{x}" y="{yy}" width="90" height="31" rx="3" class="pending-bar"/><text x="{x + 13}" y="{yy + 22}" class="axis">待测</text>')
    return '<article class="chart"><div class="chart-title">' + escape(title) + '<span>' + escape(unit) + ' · 越低越好</span></div><svg viewBox="0 0 970 210" role="img" aria-label="' + escape(title) + '">' + "".join(parts) + '</svg></article>'


def summary(data, cases):
    if data.get("status") == "NOT_COMPARABLE":
        return "对照条件未通过核对，暂不下性能结论。", ["保留原始结果，先核对失败或输入／输出／顺序差异；不静默筛掉异常请求。"]
    available = [key for key, _ in CASES if key in cases]
    if len(available) < 3:
        return "三组结果尚未齐，当前不能判断LMCache收益。", [
            "已完成组展示实测值，尚未完成组标为待测；待测不等于零耗时。",
            "只有相同389条原文、并发4、预热0及共同服务条件都对齐，才能给出接入前后结论。"]
    lines = []
    for ref_key, cand_key, label in (("off", "async_on", "原异步ON相比OFF"),
                                    ("async_on", "optimized_on", "同步＋提前读取ON相比原异步ON")):
        details = []
        for field, metric, _, _ in CORE:
            delta = reduction(cases[ref_key]["official"].get(field), cases[cand_key]["official"].get(field))
            if delta is not None:
                details.append(metric + ("降低" if delta >= 0 else "增加") + f"{abs(delta):.2f}%")
        lines.append(label + "：" + "，".join(details) + "。")
    return "三组已齐：先看同形态OFF→ON，再看组合修改是否改善原异步ON。", lines


def server_metrics_table(bundle):
    """Render the statistics script's validated deltas; never infer device timing."""
    if bundle.get("schema") != "ks-three-cases-server-metrics-v1":
        raise ValueError("服务端汇总schema不支持，拒绝猜测字段")
    sources = {entry["key"]: entry for entry in bundle.get("cases", [])}
    nodes = {}
    for key, entry in sources.items():
        metrics = entry.get("metrics", {})
        if metrics.get("schema") != "ks-server-prometheus-deltas-v1":
            raise ValueError("单用例服务端指标schema不支持：" + key)
        p = next((node for node in metrics.get("nodes", []) if node.get("name") == "P 18100"), {})
        nodes[key] = {"P": p, "D": metrics.get("weighted_D", {})}

    def counter(node, field):
        metric = node.get("counters", {}).get(field, {})
        return metric.get("delta") if metric.get("status") == "DELTA_VALID" and number(metric.get("delta")) else None

    def histogram(node, field, total=False):
        metric = node.get("histograms", {}).get(field, {})
        total_value, count = metric.get("sum_delta"), metric.get("count_delta")
        if metric.get("status") != "DELTA_VALID" or not number(total_value) or not number(count) or count <= 0:
            return None
        return total_value if total else total_value / count

    def ratio(node, numerator, denominator):
        left, right = counter(node, numerator), counter(node, denominator)
        return left / right if number(left) and number(right) and right > 0 else None

    specs = [
        ("P 外部命中token／查询token（%）", "P", "ratio", ("external_hits", "external_queries"), 100),
        ("P 外部命中token总数", "P", "counter", "external_hits", 1),
        ("P 外部查询token总数", "P", "counter", "external_queries", 1),
        ("P 本轮新计算KV（token总数）", "P", "hist_total", "request_prefill_kv_computed_tokens", 1),
        ("P 每请求新计算KV（token）", "P", "hist", "request_prefill_kv_computed_tokens", 1),
        ("P 首次WAITING排队均值（秒）", "P", "hist", "request_queue_time_seconds", 1),
        ("P Prefill阶段均值（秒，含等待）", "P", "hist", "request_prefill_time_seconds", 1),
        ("P 服务端TTFT均值（秒）", "P", "hist", "time_to_first_token_seconds", 1),
        ("P 抢占次数增量", "P", "counter", "preemptions", 1),
        ("D Mooncake外部命中／查询（%）", "D", "ratio", ("external_hits", "external_queries"), 100),
        ("D Mooncake命中token总数", "D", "counter", "external_hits", 1),
        ("D Mooncake查询token总数", "D", "counter", "external_queries", 1),
        ("D 本轮新计算KV（token总数）", "D", "hist_total", "request_prefill_kv_computed_tokens", 1),
        ("D 每请求新计算KV（token）", "D", "hist", "request_prefill_kv_computed_tokens", 1),
        ("D 加权首次WAITING排队（秒）", "D", "hist", "request_queue_time_seconds", 1),
        ("D 加权Prefill阶段（秒，含等待）", "D", "hist", "request_prefill_time_seconds", 1),
        ("D 加权Decode阶段（秒，含等待）", "D", "hist", "request_decode_time_seconds", 1),
        ("D 加权服务端TTFT（秒）", "D", "hist", "time_to_first_token_seconds", 1),
        ("D 加权服务端TPOT（毫秒）", "D", "hist", "request_time_per_output_token_seconds", 1000),
        ("D MTP接受token／草拟token（%）", "D", "ratio", ("mtp_accepted_tokens", "mtp_draft_tokens"), 100),
        ("D MTP每次草拟平均接受token", "D", "ratio", ("mtp_accepted_tokens", "mtp_drafts"), 1),
        ("D 抢占次数增量", "D", "counter", "preemptions", 1),
    ]
    rows = []
    for label, role, kind, field, scale in specs:
        cells = []
        for key, _ in CASES:
            node = nodes.get(key, {}).get(role, {})
            if kind == "counter": value = counter(node, field)
            elif kind == "ratio": value = ratio(node, *field)
            else: value = histogram(node, field, total=kind == "hist_total")
            cells.append(display(value, scale) if number(value) else '<span class="muted">未验证</span>')
        rows.append([escape(label)] + cells)
    result = table(["服务端指标"] + [label for _, label in CASES], rows)
    result += '<p class="notice">时间均值＝本轮直方图sum增量／count增量；四D采用统计文件的weighted_D合计，再除以总count，并非四个接口均值的简单平均。Prefill／Decode是当前版本定义的服务端阶段，包含等待；不将P＋D Prefill相加解释成纯算子或纯设备计算。D外部命中表示Mooncake接收P侧KV，不是D的LMCache命中率。</p>'
    result += '<details><summary>服务端增量来源（未验证字段不补零）</summary>'
    for key, label in CASES:
        if key in sources:
            result += '<p class="path">' + escape(label) + '：' + escape(sources[key].get("source", "未提供")) + '</p>'
            for node in sources[key]["metrics"].get("nodes", []):
                result += '<p class="path">' + escape(node.get("name", "")) + '<br>before：' + escape(node.get("before", "未提供")) + '<br>after：' + escape(node.get("after", "未提供")) + '</p>'
    return result + '</details>'


def interpretation_section(metrics_source=None):
    timing = PREP / "分析/优化ON_生效与计时边界.md"
    text_boundary = PREP / "分析/输出文本一致性边界.md"
    old_new = PREP / "分析/新旧用例差异_来源核对.md"
    def source_link(path, label):
        relative = "../" + path.relative_to(PREP).as_posix()
        return '<a href="' + escape(relative) + '">' + escape(label) + '</a>' if path.is_file() else escape(label + "（来源未提供）")
    parts = ['<h3>为什么本轮收益与旧4K用例不同</h3><p>旧4K四并发是合成random数据：每条4096输入／256输出、正式32条，已全前缀命中但仍计算最后1token；本轮是389条客户原文，输入261—48030、均值约10045，原异步实测token命中65.3545%，平均仍新算约3480token。长输入省掉的处理更多，客户2—4K分桶仍略慢、长输入分桶改善明显，因此整体转正不表示旧等待机制消失。旧OFF与ON还存在跨日、跨机器和源码／观测差异，只能作为历史参考；' + source_link(old_new, '新旧用例来源与可比边界') + '。</p>']
    parts.append('<h3>阶段计时不能直接当作纯计算拆账</h3><p><b>同步恢复会改变排队与Prefill的计时归属。</b>异步路径先以远程KV等待状态停留；同步路径先记入执行安排，再在worker中等待恢复。因此queue下降不等于全部节省了完成通知等待，Prefill上升也不等于算子变慢。</p>')
    parts.append('<p>现有trace证明后段READ已在PP接收前提交，后续复用同一future。单条部分命中样本的PP接收约311毫秒，<b>不是净节省311毫秒</b>；缺少同请求同批状态重放及缓存端设备完成关联。' + source_link(timing, '候选生效、统计公式与源码行号') + '。</p>')
    if metrics_source:
        bundle = json.loads(metrics_source.read_text(encoding="utf-8"))
        server = {entry["key"]: entry.get("metrics", {}) for entry in bundle.get("cases", [])}
        def node(key, role):
            raw = server.get(key, {})
            return raw.get("weighted_D", {}) if role == "D" else next((n for n in raw.get("nodes", []) if n.get("name") == "P 18100"), {})
        def mean(key, role, field):
            value = node(key, role).get("histograms", {}).get(field, {})
            total, count = value.get("sum_delta"), value.get("count_delta")
            return total / count if value.get("status") == "DELTA_VALID" and number(total) and number(count) and count > 0 else None
        def counter(key, role, field):
            value = node(key, role).get("counters", {}).get(field, {})
            return value.get("delta") if value.get("status") == "DELTA_VALID" and number(value.get("delta")) else None
        p_changes = []
        for field, label in (("request_prefill_time_seconds", "P Prefill阶段"), ("request_queue_time_seconds", "P首次WAITING排队"), ("time_to_first_token_seconds", "P服务端TTFT")):
            left, right = mean("off", "P", field), mean("async_on", "P", field)
            if number(left) and number(right):
                difference = (right - left) * 1000
                p_changes.append(label + ("增加" if difference > 0 else "减少") + f"{abs(difference):.1f}毫秒")
        hit, query = counter("async_on", "P", "external_hits"), counter("async_on", "P", "external_queries")
        if p_changes:
            hit_text = f"，P实际外部token命中率{hit / query * 100:.4f}%" if number(hit) and number(query) and query > 0 else ""
            parts.append('<p><b>原异步ON相对OFF已观察到：</b>' + escape("、".join(p_changes) + hit_text) + '。这些是各自边界的墙钟统计，不能简单相加拆成纯计算、纯搬运或通知等待。</p>')
        d_changes = []
        for field, label in (("request_decode_time_seconds", "D Decode阶段"), ("request_time_per_output_token_seconds", "D服务端TPOT")):
            change = reduction(mean("off", "D", field), mean("async_on", "D", field))
            if change is not None:
                d_changes.append(label + ("降低" if change >= 0 else "增加") + f"{abs(change):.2f}%")
        accepted = [counter(key, "D", "mtp_accepted_tokens") for key in ("off", "async_on")]
        drafted = [counter(key, "D", "mtp_draft_tokens") for key in ("off", "async_on")]
        if all(number(value) for value in accepted + drafted) and all(value > 0 for value in drafted):
            delta_pp = (accepted[1] / drafted[1] - accepted[0] / drafted[0]) * 100
            d_changes.append("MTP接受率" + ("提高" if delta_pp >= 0 else "降低") + f"{abs(delta_pp):.3f}个百分点")
        if d_changes:
            parts.append('<p><b>D侧：</b>' + escape("、".join(d_changes)) + '。方向一致可作为解释线索，尚未证明因果；请求抵达节奏与批次组成也可能影响这些值。</p>')
    parts.append('<p><b>输出全文精度尚未验收。</b>OFF64与OFF48共有389条原请求也仅13条全文完全一致。因此ON全文不同不能孤立判为缓存错误；这同样不能证明ON正确或排除缓存问题。' + source_link(text_boundary, '输出文本一致性参考边界') + '。</p>')
    parts.append('<p class="notice"><b>补验不替代主对照：</b>22:31启动的优化同P、整轮空缓存repeat2独立保存；后续P profiler拟采客户原行36的cold／warm，经正常代理由D消费。本页仍使用已完成原三组及其metrics，尚未接入补验结果，不预填补验数值。</p>')
    return "".join(parts)


def outlier_section(cases):
    if not all(key in cases for key in ("async_on", "optimized_on")):
        return ""
    left = {row["source_line"]: row for row in cases["async_on"].get("per_request", [])}
    right = {row["source_line"]: row for row in cases["optimized_on"].get("per_request", [])}
    paired = [(right[line]["ttft_s"] - row["ttft_s"], row, right[line]) for line, row in left.items()
              if line in right and number(row.get("ttft_s")) and number(right[line].get("ttft_s"))]
    if not paired:
        return ""
    delta, original, optimized = max(paired, key=lambda item: item[0])
    net = sum(item[0] for item in paired)
    async_metrics, optimized_metrics = cases["async_on"]["official"], cases["optimized_on"]["official"]
    mean_change = reduction(async_metrics.get("mean_ttft_ms"), optimized_metrics.get("mean_ttft_ms"))
    median_change = reduction(async_metrics.get("median_ttft_ms"), optimized_metrics.get("median_ttft_ms"))
    paragraph = '<p><b>TTFT均值与中位数出现不同方向。</b>'
    if mean_change is not None and median_change is not None:
        paragraph += escape('优化相对原异步：平均TTFT' + ('改善' if mean_change >= 0 else '变慢') + f'{abs(mean_change):.2f}%，中位数' + ('改善' if median_change >= 0 else '变慢') + f'{abs(median_change):.2f}%。')
    paragraph += '最大差值请求仍保留在389条主表中，不能剔除后宣称正式成绩改善。</p>'
    identity = '客户原行' + str(optimized['source_line']) + '，输入' + str(optimized.get('glm_input_tokens', '未提供')) + '／原输出' + str(optimized.get('original_output_tokens', '未提供'))
    rows = [[escape(identity), display(original['ttft_s'], digits=6), display(optimized['ttft_s'], digits=6), f'{delta:+.6f}'],
            ['其他' + str(len(paired) - 1) + '条合计', '—', '—', f'{net - delta:+.6f}'],
            ['完整' + str(len(paired)) + '条净差合计', '—', '—', f'{net:+.6f}']]
    paragraph += table(['TTFT差值拆账（秒）', '原异步', '优化', '优化−异步'], rows)
    if net:
        paragraph += '<p>这条请求贡献净变慢总量的' + f'{delta / net * 100:.2f}%' + '；其他请求合计抵消了一部分。这是完整数据的差值拆账，<b>不剔除该条，也不重定义正式成绩</b>。</p>'
    paragraph += '<p><b>长尾的直接异常链现已确认在P→D传输连接建立。</b>P从lookup到最终结果更新仅约0.525秒；D0／TP0的一份同步读取窗口为11637.32毫秒，出现HCCL申请659456字节设备内存失败→连接等待10000毫秒超时→内部重试成功。这个窗口包含连接与重试，不是纯KV字节搬运；请求最后成功仍可能已拖慢首token。</p>'
    paragraph += '<p><b>仍为NOT_VERIFIED：</b>哪一端的设备余量、容量参数、碎片或通道资源导致申请失败。不能把D的4.5 GiB KV池写成已证实根因，也不能宣称候选与资源时序完全无关。<a href="../分析/优化ON_原行57极慢请求.md">最新请求链、原始日志与HCCL证据</a>；<a href="../分析/正式三组对照/三组最终/优化TTFT离群解析.md">离群数值拆账（原因以最新定位为准）</a>。</p>'
    return paragraph


def build(data, source, output, metrics_source=None, comparison_md=None, failed_attempts=()):
    cases = {case["key"]: case for case in data.get("cases", [])}
    title, findings = summary(data, cases)
    complete = all(key in cases for key, _ in CASES) and data.get("status") == "COMPARABLE_389_ALL_THREE"
    status = "三组389条正式结果已齐" if complete else "条件待核对" if data.get("status") == "NOT_COMPARABLE" else "等待ON正式结果"
    heat_caveat = '<p class="notice"><b>主结论边界：</b>原异步主结果是同一P已跑过完整首轮后的repeat2，缓存已清空；优化组为fresh P，只做过冷缓存最长诊断。<b>缓存初态相同，不等于进程热度完全相同。</b>以下性能是本轮观测，不能严称候选修改的纯因果收益。极慢请求的直接异常链已定位到HCCL连接失败／超时重试，底层资源失败原因仍未确定。</p>' if complete else ''
    charts = "".join(chart(field, name, unit, scale, cases) for field, name, unit, scale in CORE)
    rows = []
    for label, field, scale in metric_rows():
        rows.append([escape(label)] + [display(cases.get(key, {}).get("official", {}).get(field), scale) for key, _ in CASES])
    full_metrics = table(["指标"] + [label for _, label in CASES], rows)
    improvement = []
    pairs = (("off", "async_on"), ("off", "optimized_on"), ("async_on", "optimized_on"))
    for field, label, _, _ in CORE:
        values = []
        for ref, cand in pairs:
            left = cases.get(ref, {}).get("official", {}).get(field)
            right = cases.get(cand, {}).get("official", {}).get(field)
            delta = reduction(left, right) if data.get("status") != "NOT_COMPARABLE" else None
            values.append(percentage(delta))
        improvement.append([escape(label)] + values)
    improvement_table = table(["均值降幅", "原异步ON vs OFF", "组合优化ON vs OFF", "组合优化ON vs 原异步ON"], improvement)
    case_status = table(["用例", "正式结果", "对齐状态"], [[escape(label),
        (display(cases[key].get("official", {}).get("completed")) + "／" + display(data.get("expected_count"))) if key in cases else "待测",
        escape(cases[key].get("status", "未提供")) if key in cases else "待测"] for key, label in CASES])
    setup = table(["条件", "三组共同保持"], [
        ["客户数据", "原500条中按GLM输入＋原输出≤49152保留389条（77.8%），原文、原序及逐条输出不改"],
        ["压测", "C4，预热0，seed=0，逐条原输出100—891 token，禁补采样"],
        ["P", "DP1／TP8／PP2，41,37层，DSA CP=true，MTP3；49152／0.94；batch16384"],
        ["D", "DP4／TP4／PP1，MTP3；65536／0.92＋显式KV4.5 GiB，四D持续保留"],
        ["传输与缓存", "P→D原Mooncake；APC关闭；共同常驻12 GiB LMCache，OFF的Connector不调用它"],
        ["源码与观测", "既有17行适配、pin及共同观测保留；原私有runtime／venv，未叠加vLLM提前通知候选"]])
    differences = table(["差异", "不接LMCache", "原异步ON", "同步＋提前读取ON"], [
        ["P Connector", "Mooncake", "Multi：Mooncake＋LMCache MP", "同原异步ON"],
        ["同步恢复开关", "不适用", "不设置", "true"],
        ["后段提前READ开关", "不适用", "不设置", "true"],
        ["P导入顺序", "baseline", "baseline", "prefetch→poll→baseline"]])
    supplemental = '<p class="muted">服务端Prefill／Decode与实际命中率等待独立metrics汇总；不由客户端指标推算。</p>'
    if metrics_source:
        supplemental = '<p class="path">补充来源：' + escape(str(metrics_source)) + '</p>' + server_metrics_table(json.loads(metrics_source.read_text(encoding="utf-8")))
    evidence = '<p class="path">正式对照JSON：' + escape(str(source)) + '</p>'
    for key, label in CASES:
        if key in cases:
            evidence += '<p class="path">' + escape(label) + '：' + escape(cases[key].get("source", "来源未提供")) + '</p>'
    if comparison_md and comparison_md.is_file():
        evidence += '<details><summary>统计脚本原始说明</summary><pre class="prose">' + escape(comparison_md.read_text(encoding="utf-8")) + '</pre></details>'
    failed_section = ""
    if failed_attempts:
        failed_rows = []
        failed_commands = []
        manual = PREP / "实际验证配置/最简手工复现.md"
        blocks = re.findall(r"```bash\n(.*?)```", manual.read_text(encoding="utf-8"), re.S) if manual.is_file() else []
        original_async = next((block.strip() for block in blocks if "vllm bench serve" in block and "/stages/on-async/" in block), None)
        for path in failed_attempts:
            attempt = json.loads(path.read_text(encoding="utf-8"))
            failed_rows.append([escape(path.name), display(attempt.get("completed")), display(attempt.get("failed")),
                '<span class="path">' + escape(str(path)) + '</span>'])
            if original_async:
                failed_commands.append('<details><summary>含失败异步首轮 · 原生benchmark（独立归档）</summary>' + highlight(benchmark_for_source(original_async, path)) + '</details>')
        failed_section = '<h3>保留的含失败原轮：不进入成功复跑主比较</h3><p>主表使用完整成功复跑。以下原轮独立归档，不删除失败请求，也不把其中成功请求裁剪后混入主表；具体失败原因须回查其客户端／服务端证据。</p>' + table(["归档", "成功", "失败", "原件路径"], failed_rows)
        failed_section += "".join(failed_commands)
    notes = [
        "TTFT是客户端发出请求到首个有效输出流块，包含调度、缓存、传输及D首输出，不能全部称P计算。",
        "TPOT按首输出后的时间与实际输出token数摊分，不是D纯解码或单个D算子的计时。MTP的流块ITL也不是逐token间隔。",
        "D external prefix计数表示Mooncake接入P侧KV，不能称D的LMCache命中率。客户metadata前缀比例也不是实际LMCache命中率。",
        "均值降幅=(参照均值−候选均值)/参照均值；正值表示耗时减少，不是逐条百分比的均值，也未声称统计显著性。",
        "优化ON同时改变同步恢复与后段提前读取；三组可证明组合效果，不能单独分离两项贡献。",
        "实际采集经过既有client_observe入口并产生client.jsonl；原生vllm bench复现请求参数，不自动包含同样维测。额外观测开销未单独量化。",
        "服务日志按runs中的角色／容量归档；内部trace仍在stages/phase/traces，须用请求ID和时间关联，不能混入旧64K或历史合成数据。",
        "OFF64的408条成功与ON64的内部OOM是独立容量结果，不与48K389条混作主对照。KS-MEM-001保持OPEN。"]
    body = f'''<header><div class="eyebrow">KS · 客户原文 · PP=2 · 正式三组对照</div><h1>接入LMCache，<br>这一轮究竟改变了什么？</h1><div class="status">{escape(status)}</div><p class="lead">{escape(title)}</p><ul class="findings">{''.join('<li>'+escape(line)+'</li>' for line in findings)}</ul>{heat_caveat}<p class="muted">生成时间 {escape(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))} · 只展示已完成来源；未测数据留空</p></header>
<nav><a href="#effect">直接效果</a><a href="#metrics">完整指标</a><a href="#setup">对照条件</a><a href="#interpretation">机制与边界</a><a href="#repro">原生命令</a><a href="#evidence">证据边界</a></nav>
<section id="effect"><div class="section-number">01</div><h2>先看最直接的三个效果</h2><p>每张图单位独立，三组使用同一坐标范围。待测栏只标状态，不当作零值绘图。</p>{charts}<h3>同一指标，换算成改善百分比</h3><p>下表是均值降幅：正数为改善，负数为变慢。先看接入前后，再看修改能否改善接入后的结果。</p>{improvement_table}</section>
<section id="metrics"><div class="section-number">02</div><h2>指标为行，用例为列</h2>{case_status}{full_metrics}<h3>服务端阶段与缓存计数</h3>{supplemental}</section>
<section id="setup"><div class="section-number">03</div><h2>这轮可以比较什么</h2><p>可验证的问题：在同一客户原文序列和计算形态下，P接入LMCache后，TTFT、TPOT和端到端耗时如何变化；组合修改是否改善原异步ON。</p>{setup}<h3>三组只保留这些接入差异</h3>{differences}<p class="notice">D继续64K／4.5 GiB，未执行预备48K D／3.5 GiB；较大的D上限不改变客户请求的实际长度。服务条件来自launch／观测证据，benchmark JSON本身不能证明全部服务状态。</p></section>
<section id="interpretation"><div class="section-number">04</div><h2>如何解释变化，哪些仍不能归因</h2>{outlier_section(cases)}{interpretation_section(metrics_source)}</section>
<section id="repro"><div class="section-number">05</div><h2>可折叠的完整原生复现</h2>{repro_section(cases)}</section>
<section id="evidence"><div class="section-number">06</div><h2>能得出什么，仍不能得出什么</h2><ul class="boundaries">{''.join('<li>'+escape(note)+'</li>' for note in notes)}</ul>{failed_section}{evidence}</section><footer>报告由本地正式数据生成；没有补造ON、命中率或服务端阶段值。实验复盘结构用于区分事实、解释和未知，不作产品上线判断。</footer>'''
    css = '''*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f2f1eb;color:#25322f;font-family:"Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;line-height:1.75}main{max-width:1180px;margin:auto;padding:40px 24px 70px}header{padding:42px;background:#fffdf8;border-top:5px solid #31806c;border-bottom:1px solid #d9ded6}.eyebrow{font-size:13px;letter-spacing:.09em;color:#62746b;font-weight:700}h1{font-size:clamp(30px,4.6vw,54px);line-height:1.2;letter-spacing:-.04em;margin:22px 0}h2{font-size:28px;line-height:1.4;margin:4px 0 20px}h3{font-size:19px;margin:30px 0 12px}h4{font-size:15px}p{margin:10px 0 18px}.lead{font-size:22px;max-width:900px}.status{display:inline-block;border:1px solid #a7b6ab;background:#eef4ed;color:#406d59;border-radius:4px;padding:4px 12px;font-size:14px}.findings{padding-left:22px;font-size:18px;max-width:960px}.findings li{margin:8px 0}.muted{color:#718078;font-size:13px}nav{display:flex;gap:24px;flex-wrap:wrap;padding:18px 0;border-bottom:1px solid #d9ded6}nav a{color:#42675a;text-decoration:none;font-size:14px;font-weight:700}section{background:#fffdf8;margin-top:24px;padding:34px 36px;border:1px solid #d9ded6;scroll-margin-top:18px}.section-number{font-size:12px;color:#718078;letter-spacing:.12em}.chart{margin:18px 0 24px;border:1px solid #dde3db;padding:18px 20px;background:#fff}.chart-title{display:flex;justify-content:space-between;align-items:center;font-size:18px;font-weight:700}.chart-title span{font-size:12px;color:#7d867e;font-weight:400}svg{display:block;width:100%;height:auto;margin-top:12px}.gridline{stroke:#e8ece7;stroke-width:1}.axis{fill:#7b867f;font-size:13px}.bar-label{font-size:15px;fill:#35473f}.bar-value{font-size:14px;fill:#35473f;font-weight:700}.pending-bar{fill:#f6f7f3;stroke:#bcc7bd;stroke-dasharray:4 4}.table-wrap{overflow:auto;margin:18px 0 24px;border:1px solid #dce2d9}table{width:100%;border-collapse:collapse;min-width:740px}th,td{padding:11px 14px;border-bottom:1px solid #e5e9e1;text-align:right;font-size:14px;vertical-align:top}thead th{background:#edf1e8;color:#52675a;font-weight:700}th:first-child{text-align:left;min-width:220px}tbody th{font-weight:500;color:#44564c;background:#fcfcf7}tr:last-child th,tr:last-child td{border-bottom:0}tbody tr:hover{background:#f7f9f2}.good{color:#27775c;font-weight:700}.bad{color:#ac574b;font-weight:700}.neutral{color:#607467}details{border:1px solid #dce2d9;margin:12px 0;background:#fff}summary{cursor:pointer;padding:13px 16px;color:#3d6251;font-weight:700}details>p,details>h4{margin-left:18px;margin-right:18px}pre{overflow-x:auto;white-space:pre;overflow-wrap:normal;word-break:normal;background:#202a2d;color:#e5e9e8;font:13px/1.8 Consolas,"Cascadia Code",monospace;padding:20px;margin:0 0 18px;tab-size:4}pre.prose{background:#f7f8f3;color:#43574b;white-space:pre-wrap;line-height:1.8;font-family:inherit}.syn-flag{color:#92c8ef}.syn-string{color:#c0d69c}.syn-number{color:#e7bd83}.syn-keyword{color:#ceafd9}.path{font:12px/1.8 Consolas,monospace;color:#708075;overflow-wrap:anywhere}.notice{border-left:3px solid #b59150;background:#fbf6e9;padding:12px 18px}.boundaries{padding-left:22px}.boundaries li{margin:12px 0}footer{padding:30px 0 0;color:#718078;font-size:12px}@media(max-width:700px){main{padding:12px}header,section{padding:24px 18px}h2{font-size:24px}.lead{font-size:19px}nav{gap:16px}.chart{padding:12px}th,td{font-size:12px;padding:9px 10px}}'''
    output.write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>客户原文_PP2_LMCache三组对照</title><style>' + css + '</style></head><body><main>' + body + '</main></body></html>', encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison-json", type=Path, default=PREP / "分析/正式三组对照/三组最终/正式三组对照.json")
    parser.add_argument("--comparison-md", type=Path)
    parser.add_argument("--metrics-json", type=Path)
    parser.add_argument("--failed-attempt-json", type=Path, action="append", default=[], help="含失败首轮的原始benchmark JSON；单列归档，不进入主比较")
    parser.add_argument("--output", type=Path, default=HERE / "客户原文_PP2_LMCache三组对照.html")
    args = parser.parse_args()
    if not args.comparison_json.is_file():
        parser.error("对照JSON尚不存在；请用--comparison-json指定已生成的真实结果，缺少ON可只传OFF统计文件。")
    data = json.loads(args.comparison_json.read_text(encoding="utf-8"))
    if data.get("schema") != "ks-customer-48k-off-on-formal-comparison-v1":
        parser.error("不支持的对照数据schema，拒绝猜测字段或补造数据。")
    if args.metrics_json and not args.metrics_json.is_file():
        parser.error("metrics JSON不存在。")
    if args.output.resolve() == (HERE / "客户原文_PP2_LMCache三组对照.html").resolve() and data.get("status") != "COMPARABLE_389_ALL_THREE":
        parser.error("默认最终报告只接受三组完整可比结果；若需验证待测页面，请显式--output指定临时草稿路径。")
    for path in args.failed_attempt_json:
        if not path.is_file() or not number(json.loads(path.read_text(encoding="utf-8")).get("failed")) or json.loads(path.read_text(encoding="utf-8"))["failed"] <= 0:
            parser.error("含失败原轮必须提供真实且failed>0的原始benchmark JSON。")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    comparison_md = args.comparison_md or args.comparison_json.with_suffix(".md")
    build(data, args.comparison_json, args.output, args.metrics_json, comparison_md, args.failed_attempt_json)
    print(json.dumps({"output": str(args.output.resolve()), "comparison_source": str(args.comparison_json.resolve()),
        "status": data.get("status"), "available_cases": [case["key"] for case in data.get("cases", [])],
        "metrics_source": str(args.metrics_json) if args.metrics_json else None, "remote_actions": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
