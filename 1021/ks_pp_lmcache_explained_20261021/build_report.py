"""Build a standalone report from already archived text evidence. No remote IO."""
from pathlib import Path
import json
from html import escape

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "PP与LMCache_机制可视化报告.html"


def embedded_json(filename):
    value = json.loads((ROOT / filename).read_text(encoding="utf-8"))
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def main():
    html = (ROOT / "report.template.html").read_text(encoding="utf-8")
    approved = (ROOT.parent / "ks_p_local_m12_m137_20261016/patched17-validation-20261016/p/approved17.patch").read_text(encoding="utf-8")
    approved = approved[approved.index("diff --git"):]
    inserts = {
        "__REPORT_CSS__": (ROOT / "report.css").read_text(encoding="utf-8"),
        "__REPORT_JS__": (ROOT / "report.js").read_text(encoding="utf-8"),
        "__REPORT_DATA__": embedded_json("report-data.json"),
        "__SOURCE_DATA__": embedded_json("source_code.json"),
        "__COMMANDS_DATA__": embedded_json("baseline_commands.json"),
        "__GLOSSARY_DATA__": embedded_json("glossary.json"),
        "__APPROVED_PATCH__": escape(approved),
    }
    for token, text in inserts.items():
        if html.count(token) != 1:
            raise ValueError(f"Expected one insertion point: {token}")
        html = html.replace(token, text)
    if "\ufffd" in html:
        raise ValueError("Unexpected Unicode replacement character")
    OUTPUT.write_text(html, encoding="utf-8", newline="\n")
    print(OUTPUT)
    print(f"standalone_bytes={OUTPUT.stat().st_size}")


if __name__ == "__main__":
    main()
