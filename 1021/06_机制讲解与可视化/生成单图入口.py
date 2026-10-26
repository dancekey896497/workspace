"""将已有自制图内嵌到可见、无锚点的本地HTML，不修改原图或启动服务。"""
from pathlib import Path
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
source = HERE.parent.parent / "pp_lmcache_sequence.html"
document = BeautifulSoup(source.read_text(encoding="utf-8"), "html.parser")
picture = document.select_one("#sequence img")
assert picture is not None
image = picture["src"]
assert image.startswith("data:image/png;base64,")
target = HERE / "pp_lmcache_one_diagram.html"
page = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PP＋LMCache：一张图看懂谁在算、谁在等</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#273c57;font:16px/1.85 "Microsoft YaHei",sans-serif}main{max-width:1200px;margin:auto;padding:22px 18px 45px}h1{font-size:26px;line-height:1.5;margin:0 0 12px}p{margin:8px 0 15px;color:#58708d}.picture{background:white;border:1px solid #d9e3ef;border-radius:6px;overflow:auto}.picture img{display:block;width:100%;height:auto}.note{font-size:13px;padding:13px 16px;background:#edf3fb;border-radius:5px}a{color:#2669aa}footer{font-size:13px;color:#637b96;margin-top:17px}@media(max-width:700px){h1{font-size:22px}main{padding:17px 10px}.picture img{min-width:900px}}</style></head>
<body><main><h1>PP＋LMCache：一张图看懂谁在算、谁在等</h1>
<p>沿五列角色从上往下看。紫色是模型中间结果或输出，绿色是KV完成消息，橙色展开核心还没有处理完成消息的等待。</p>
<div class="picture"><img id="sequence-image" src="__PICTURE__" alt="调度核心、调度器、执行器、前后段worker的完整两请求时序图" onload="this.dataset.loaded='true';this.dataset.width=this.naturalWidth;this.dataset.height=this.naturalHeight" onerror="this.dataset.loaded='false'"></div>
<p class="note">这是之前那张自制时序图，保留原有表达和历史记录。纵向距离不表示实际时间比例，局部就绪不等于所有分片同时完成，也不能把其中全部等待当成PP2相对PP1新增或可全部删除的时间。小屏可以左右滑动查看。</p>
<footer><a href="批次与数据调度_图解.html">需要时再打开交互图解与更新后的实测边界</a>。本文件已内嵌图片，无需旁边另有PNG，也无需联网。</footer>
</main></body></html>'''.replace("__PICTURE__", image)
target.write_text(page, encoding="utf-8")
assert not (target.stat().st_file_attributes & 2), "新入口不能有隐藏属性"
assert "\ufffd" not in page
print(f"VISIBLE_HTML: {target}")
print("PASS: 普通可见文件；图片内嵌；无需锚点或外部图片。")
