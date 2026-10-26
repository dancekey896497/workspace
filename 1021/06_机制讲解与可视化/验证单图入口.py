"""本机浏览器检查纵向时间图；不截图、不连接实验环境。"""
from pathlib import Path
import json
import subprocess
import tempfile
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

here = Path(__file__).resolve().parent
target = here / "pp_lmcache_one_diagram.html"
assert target.is_file() and not target.stat().st_file_attributes & 2
profile = tempfile.mkdtemp(prefix="ks-one-diagram-edge-")
result = subprocess.run([
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
    "--disable-background-networking", f"--user-data-dir={profile}",
    "--window-size=1440,1000", "--virtual-time-budget=1200", "--dump-dom", target.as_uri(),
], capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")[-600:]
document = BeautifulSoup(result.stdout.decode("utf-8"), "html.parser")
check = document.select_one("#render-check")
assert check is not None, "浏览器未完成绘制检查"
layout = json.loads(check.string)
assert layout["svgCount"] == 1 and layout["textCount"] > 60, layout
assert not layout["overflow"] and not layout["overlap"], layout
assert not layout["pageOverflow"] and not layout["externalRequests"], layout
missing = []
for a in document.select("a[href]"):
    url = urlparse(a["href"])
    if url.scheme == "file" and not Path(unquote(url.path).lstrip("/")).is_file():
        missing.append(a["href"])
assert not missing, missing
receipt = {"status": "PASS", "file": str(target), "hidden": False,
           "url_has_fragment": False, "layout": layout,
           "missing_links": missing, "screenshot_saved": False}
(here / "单图入口验证.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(receipt, ensure_ascii=False))
