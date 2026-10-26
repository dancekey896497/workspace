#!/usr/bin/env python3
"""仅将本地综合说明渲染为独立HTML；不联网、不操作实验或数据原件。"""
import argparse
from datetime import datetime
import html
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent


def esc(text):
    return html.escape(str(text), quote=True)


def inline(text):
    text = esc(text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    text = re.sub(r"`([^`]+)`", r'<code class="inline">\1</code>', text)
    return re.sub(r"\*\*(.+?)\*\*", r'<strong>\1</strong>', text)


def code_html(text):
    token = re.compile(r"#[^\n]*|'[^']*'|\"(?:\\.|[^\"\\])*\"|\b(?:with|if|not|None)\b|\b\d+(?:\.\d+)?\b")
    out = []; end = 0
    for match in token.finditer(text):
        out.append(esc(text[end:match.start()]))
        value = match.group()
        css = "comment" if value.startswith("#") else "string" if value[:1] in ("'", '"') else "number" if value[0].isdigit() else "keyword"
        out.append(f'<span class="{css}">{esc(value)}</span>')
        end = match.end()
    out.append(esc(text[end:]))
    return '<pre><code>' + ''.join(out) + '</code></pre>'


def svg_box(x, y, lines, fill="#edf2f5", width=145, height=76):
    result = f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="7" fill="{fill}" stroke="#cbd7d7"/>'
    for index, line in enumerate(lines):
        result += f'<text x="{x + width/2}" y="{y + 29 + index*23}" text-anchor="middle" class="box-text">{esc(line)}</text>'
    return result


def line(x1, y1, x2, y2, marker, dashed=False):
    dash = ' stroke-dasharray="6 5"' if dashed else ''
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#738d8c" stroke-width="2"{dash} marker-end="url(#{marker})"/>'


def flow_figure():
    marker = '<defs><marker id="flowArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#738d8c"/></marker></defs>'
    content = marker
    xs = [110, 275, 440, 605, 770, 935]
    rows = [(65, "OFF", [("安排全部输入", "L个token"), ("前41层", "生成本次各层KV"), ("后37层", "接中间结果再算"), ("输出层＋采样", "产生首个输出"), ("额外MTP层", "同层3次前向"), ("原Mooncake→D", "传KV，D继续输出")]),
            (235, "ON", [("查命中／恢复KV", "各PP段恢复自己KV"), ("前41层", "只处理剩余token"), ("后37层", "接中间结果再算"), ("输出层＋采样", "产生首个输出"), ("额外MTP层", "仍有prompt KV工作"), ("原Mooncake→D", "D路线保持不变")])]
    for y, label, boxes in rows:
        content += f'<text x="20" y="{y+42}" class="lane-label">{label}</text>'
        for index, (x, texts) in enumerate(zip(xs, boxes)):
            content += svg_box(x, y, texts, "#e9f2ed" if label == "ON" and index < 3 else "#edf2f5")
            if index < len(xs)-1:
                content += line(x+145, y+38, xs[index+1]-4, y+38, "flowArrow")
    content += '<text x="110" y="190" class="figure-note">减少：历史输入的重复处理。保留：模型深度、剩余位置计算、并行依赖及相应MTP工作。</text>'
    content += '<text x="110" y="350" class="figure-note">完整命中仍保留最后1token；部分命中则继续处理更多剩余输入。KV不是最终logits答案。</text>'
    return '<figure><svg viewBox="0 0 1120 380" role="img" aria-label="OFF处理全部输入，ON恢复历史KV但保留剩余token全层链">' + content + '</svg><figcaption>动作关系示意，不表示各框耗时相等；前后PP段通过中间hidden state交接，不是通过调度器逐层交接。</figcaption></figure>'


def overlap_figure():
    marker = '<defs><marker id="overlapArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8 Z" fill="#738d8c"/></marker></defs>'
    content = marker + '<text x="22" y="40" class="figure-note">逻辑关系示意：各数字为本rank host区间，不按跨rank绝对时间比例绘图。</text>'
    content += '<text x="22" y="119" class="lane-label">前41层</text>'
    content += svg_box(175, 77, ("同步恢复确认", "165.259ms"), "#e9f2ed", 195)
    content += line(374, 115, 419, 115, "overlapArrow")
    content += svg_box(425, 77, ("主模型调用包络", "281.014ms"), "#edf2f5", 230)
    content += '<text x="22" y="245" class="lane-label">后37层</text>'
    content += '<circle cx="175" cy="194" r="5" fill="#438575"/>'
    content += '<text x="192" y="199" class="figure-note">READ已提前提交；其整次设备耗时尚未单独量化</text>'
    content += line(180, 194, 670, 194, "overlapArrow", True)
    content += svg_box(175, 222, ("等待PP中间结果", "499.383ms"), "#f4eedf", 365)
    content += line(655, 153, 565, 222, "overlapArrow", True)
    content += svg_box(565, 222, ("加载边界末尾等待", "仅0.667ms"), "#e9f2ed", 180)
    content += line(749, 260, 774, 260, "overlapArrow")
    content += svg_box(780, 222, ("主模型包络", "292.560ms"), "#edf2f5", 155)
    content += line(939, 260, 960, 260, "overlapArrow")
    content += svg_box(965, 222, ("MTP草拟", "45.306ms"), "#edf2f5", 135)
    content += '<text x="175" y="340" class="figure-note">后段READ与PP等待存在重叠；0.667ms只是抵达加载边界后的剩余等待，不是READ总耗时。</text>'
    return '<figure><svg viewBox="0 0 1120 375" role="img" aria-label="后段提前读取KV并与PP接收依赖重叠">' + content + '</svg><figcaption>同步恢复确认包含于加载入口，MTP包含于末段sample；不能把嵌套框或两rank区间直接相加。</figcaption></figure>'


def render_markdown(text):
    lines = text.splitlines(); output = []; i = 0; section_id = 0
    while i < len(lines):
        item = lines[i]
        if not item.strip(): i += 1; continue
        if item.startswith('```'):
            code = []; i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i]); i += 1
            output.append(code_html('\n'.join(code))); i += 1; continue
        if item.startswith('【动作流程图'):
            output.append(flow_figure()); i += 1; continue
        if item.startswith('【观测关系图'):
            output.append(overlap_figure()); i += 1; continue
        if item.startswith('|') and i+1 < len(lines) and re.fullmatch(r'[| :\-]+', lines[i+1]):
            rows = []
            while i < len(lines) and lines[i].startswith('|'):
                rows.append([cell.strip() for cell in lines[i].strip('|').split('|')]); i += 1
            head = ''.join('<th>'+inline(cell)+'</th>' for cell in rows[0])
            body = ''.join('<tr>'+''.join(('<th>' if j==0 else '<td>')+inline(cell)+('</th>' if j==0 else '</td>') for j,cell in enumerate(row))+'</tr>' for row in rows[2:])
            output.append('<div class="table-wrap"><table><thead><tr>'+head+'</tr></thead><tbody>'+body+'</tbody></table></div>'); continue
        match = re.match(r'^(#{1,3}) (.*)', item)
        if match:
            level = len(match[1]); anchor = ''
            if level == 2:
                section_id += 1; anchor = f' id="section-{section_id}"'
            output.append(f'<h{level}{anchor}>'+inline(match[2])+f'</h{level}>'); i += 1; continue
        paragraph = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith(('#', '|', '```', '【')):
            paragraph.append(lines[i]); i += 1
        output.append('<p>'+inline(' '.join(paragraph))+'</p>')
    return ''.join(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--markdown', type=Path, default=HERE/'新旧用例与性能原理_本轮综合说明.md')
    parser.add_argument('--output', type=Path, default=HERE/'新旧用例与性能原理_本轮综合说明.html')
    args = parser.parse_args()
    text = args.markdown.read_text(encoding='utf-8')
    code_blocks = re.findall(r'```[^\n]*\n(.*?)```', text, re.S)
    assert sum(len(block.strip().splitlines()) for block in code_blocks) <= 10, '正文核心代码超过10行'
    content = render_markdown(text)
    css = '''*{box-sizing:border-box}body{margin:0;background:#efeee8;color:#24312e;font-family:"Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;line-height:1.85}main{max-width:1180px;margin:30px auto 70px;background:#fffdf8;border:1px solid #d7ded5;border-top:6px solid #3b7569;padding:35px 46px 55px}h1{font-size:36px;line-height:1.35;margin:6px 0 25px}h2{font-size:27px;line-height:1.45;margin:50px 0 20px;padding-top:24px;border-top:1px solid #dae1d8;scroll-margin-top:20px}h3{font-size:20px;margin:28px 0 12px}p{margin:16px 0}strong{color:#233f35;font-weight:700}main>p:nth-of-type(2){font-size:20px;line-height:1.85;border-left:4px solid #8ca88f;background:#f1f5ed;padding:16px 20px}a{color:#2c6a75;text-decoration:underline;text-underline-offset:3px}nav{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;padding:10px 0 20px;border-bottom:1px solid #dde3d9}.meta{font-size:12px;color:#77847d}.table-wrap{overflow-x:auto;margin:23px 0;border:1px solid #dce2d8}table{width:100%;min-width:820px;border-collapse:collapse;font-size:14px}th,td{padding:11px 14px;border-bottom:1px solid #e5e9e1;vertical-align:top;text-align:left}thead{background:#edf2e9;color:#3f5e50}thead th{font-weight:700}tbody th{font-weight:500;min-width:190px;background:#fafbf5}tr:last-child>*{border-bottom:0}tbody tr:hover{background:#f3f7ef}.inline{font:13px/1.5 Consolas,monospace;background:#edf1ec;padding:2px 5px;border-radius:3px;overflow-wrap:anywhere}pre{margin:22px 0;overflow-x:auto;white-space:pre;word-break:normal;overflow-wrap:normal;padding:21px;background:#202b2d;color:#e2e8e7;font:13px/1.9 Consolas,"Cascadia Code",monospace}.comment{color:#9bad9c}.string{color:#bfd69b}.keyword{color:#bfaddd}.number{color:#e9bf83}figure{margin:24px 0;padding:16px 12px;background:#f8faf6;border:1px solid #dce4da}svg{width:100%;height:auto;display:block}.box-text{font:14px "Microsoft YaHei",sans-serif;fill:#304a41}.lane-label{font:700 18px "Microsoft YaHei",sans-serif;fill:#365f53}.figure-note{font:13px "Microsoft YaHei",sans-serif;fill:#6f7d73}figcaption{font-size:12px;color:#768278;margin:10px 8px 0}footer{font-size:12px;color:#77847d;margin-top:40px;padding-top:18px;border-top:1px solid #dce3d9}@media(max-width:700px){main{margin:0;padding:25px 18px;border-left:0;border-right:0}h1{font-size:29px}h2{font-size:23px}main>p:nth-of-type(2){font-size:18px}nav{gap:12px}figure{padding:8px 0}table{font-size:12px}}'''
    navigation = '<nav><a href="#section-1">四列成绩</a><a href="#section-2">用例差异</a><a href="#section-3">动作图</a><a href="#section-5">单token与MTP</a><a href="#section-8">画像边界</a><a href="#section-9">优化分级</a></nav>'
    args.output.write_text('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>新旧用例与性能原理_本轮综合说明</title><style>'+css+'</style></head><body><main><div class="meta">KS · 本轮证据综合 · '+datetime.now().strftime('%Y-%m-%d %H:%M:%S')+'</div>'+navigation+content+'<footer>仅本地整理既有实测、源码与分析材料；图为依赖关系示意，不是跨rank精确时间轴。主三组与额外复跑、诊断和设备画像分别保留。</footer></main></body></html>',encoding='utf-8')
    print(str(args.output.resolve()))


if __name__ == '__main__':
    main()
