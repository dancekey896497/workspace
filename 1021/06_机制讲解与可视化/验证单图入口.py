"""本地浏览器打开单图HTML并检查原图解码；不截图，不连接实验环境。"""
from pathlib import Path
import json
import subprocess
import tempfile

from bs4 import BeautifulSoup

here = Path(__file__).resolve().parent
target = here / "pp_lmcache_one_diagram.html"
assert target.is_file() and not target.stat().st_file_attributes & 2
profile = tempfile.mkdtemp(prefix="ks-one-diagram-edge-")
result = subprocess.run([
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
    "--disable-background-networking", f"--user-data-dir={profile}",
    "--virtual-time-budget=1200", "--dump-dom", target.as_uri(),
], capture_output=True, timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")[-600:]
document = BeautifulSoup(result.stdout.decode("utf-8"), "html.parser")
picture = document.select_one("#sequence-image")
assert picture is not None
assert picture.get("data-loaded") == "true", "浏览器未确认原图加载成功"
width, height = int(picture["data-width"]), int(picture["data-height"])
assert (width, height) == (3400, 6360), (width, height)
receipt = {"status": "PASS", "file": str(target), "hidden": False,
           "url_has_fragment": False, "embedded_image_loaded": True,
           "image_width": width, "image_height": height, "screenshot_saved": False}
(here / "单图入口验证.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(receipt, ensure_ascii=False))
