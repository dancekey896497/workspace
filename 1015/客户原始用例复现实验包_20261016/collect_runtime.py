#!/usr/bin/env python3
"""Read-only collector for the current Python environment and existing cache path.

This process is not the running server. No allocation tests, requests, installs,
service changes, cache writes, or core analysis are performed. Only --output
allows writing a JSON report to the explicitly selected output directory.
"""

import argparse
import contextlib
from datetime import datetime, timezone
import importlib
import importlib.metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True


def package_info(distribution, module_name):
    report = {"distribution": distribution, "module": module_name}
    try:
        report["installed_metadata_version"] = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        report["installed_metadata_version"] = None
    try:
        spec = importlib.util.find_spec(module_name)
        report["resolved_module_path"] = spec.origin if spec else None
    except Exception as exc:
        report["resolution_error"] = str(exc)
    try:
        module = importlib.import_module(module_name)
        report["imported_module_path"] = getattr(module, "__file__", None)
        report["module_version"] = str(getattr(module, "__version__", "NOT_VERIFIED"))
        report["imported"] = True
    except Exception as exc:
        report["imported"] = False
        report["import_error"] = f"{type(exc).__name__}: {exc}"
    return report


def runtime_defaults():
    result = {"scope": "CLI defaults in this collector's imported source, not the running server's parsed config"}
    try:
        config = importlib.import_module("lmcache.v1.distributed.config")
        parser = argparse.ArgumentParser(add_help=False)
        config.add_storage_manager_args(parser)
        names = ("l1_size_gb", "l1_use_lazy", "l1_init_size_gb", "l1_align_bytes", "eviction_policy", "l2_store_policy")
        result["source_path"] = getattr(config, "__file__", None)
        result["cli_defaults"] = {name: parser.get_default(name) for name in names}
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def backend_info():
    result = {"scope": "current collector process; no tensors or device buffers allocated"}
    try:
        lmcache = importlib.import_module("lmcache")
        result["torch_device_type"] = str(getattr(lmcache, "torch_device_type", "NOT_VERIFIED"))
        device = getattr(lmcache, "torch_dev", None)
        available = getattr(device, "is_available", None)
        result["backend_available"] = bool(available()) if callable(available) else None
    except Exception as exc:
        result["backend_error"] = f"{type(exc).__name__}: {exc}"
    try:
        platform = importlib.import_module("lmcache.v1.platform")
        spec = platform.current_device_spec
        result["device_spec"] = {name: getattr(spec, name, None) for name in ("backend_name", "is_pin_supported")}
    except Exception as exc:
        result["device_spec_error"] = f"{type(exc).__name__}: {exc}"
    try:
        importlib.import_module("torch_npu")
        result["torch_npu_importable"] = True
        result["torch_npu_metadata_version"] = importlib.metadata.version("torch-npu")
    except Exception as exc:
        result["torch_npu_importable"] = False
        result["torch_npu_error"] = f"{type(exc).__name__}: {exc}"
    return result


def _mountinfo(path):
    """Find the longest mountpoint prefix; do not walk the cache directory."""
    matches = []
    text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        fields = left.split()
        tail = right.split()
        if not separator or len(fields) < 6 or len(tail) < 3:
            continue
        mountpoint = fields[4]
        for escaped, char in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
            mountpoint = mountpoint.replace(escaped, char)
        if path == mountpoint or path.startswith(mountpoint.rstrip("/") + "/"):
            matches.append({"mountpoint": mountpoint, "filesystem_type": tail[0], "source": tail[1],
                            "mount_options": fields[5], "super_options": tail[2]})
    return max(matches, key=lambda item: len(item["mountpoint"])) if matches else None


def filesystem_info(base_path):
    report = {"requested_path": str(base_path), "status": "unknown", "statx_dio_alignment": {
        "status": "NOT_VERIFIED", "reason": "This collector does not issue statx; no kernel DIO alignment is inferred from f_bsize."}}
    try:
        path = base_path.resolve(strict=True)
        if not path.is_dir():
            raise NotADirectoryError(str(path))
        report["resolved_path"] = str(path)
        stats = os.statvfs(path)
        report["statvfs"] = {name: getattr(stats, name) for name in ("f_bsize", "f_frsize", "f_blocks", "f_bavail", "f_files", "f_favail")}
        report["f_bsize_note"] = "Filesystem preferred block size; not a kernel-guaranteed O_DIRECT memory/offset alignment. The reviewed native connector uses f_bsize as its own guard."
        report["status"] = "existing_directory"
        command = shutil.which("findmnt")
        if command:
            try:
                completed = subprocess.run([command, "--json", "--target", str(path), "--output", "TARGET,SOURCE,FSTYPE,OPTIONS"],
                                           capture_output=True, text=True, timeout=10, check=False)
                if completed.returncode == 0:
                    try:
                        report["findmnt"] = json.loads(completed.stdout)
                    except ValueError:
                        report["findmnt_error"] = "findmnt did not return JSON"
                else:
                    report["findmnt_error"] = completed.stderr.strip()[:1000]
            except (OSError, subprocess.SubprocessError) as exc:
                report["findmnt_error"] = str(exc)
        if "findmnt" not in report:
            try:
                report["proc_mountinfo"] = _mountinfo(str(path))
            except OSError as exc:
                report["mountinfo_error"] = str(exc)
    except (OSError, AttributeError, subprocess.SubprocessError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-path", type=Path, help="Existing L2 directory. Never created or modified.")
    parser.add_argument("--server-log", type=Path, help="Optional existing log; bounded read-only analysis.")
    parser.add_argument("--output", type=Path, help="Explicit report directory; omitted means stdout only.")
    args = parser.parse_args()
    messages = io.StringIO()
    with contextlib.redirect_stdout(messages), contextlib.redirect_stderr(messages):
        packages = [package_info("lmcache", "lmcache"), package_info("lmcache-ascend", "lmcache_ascend")]
        native_fs = package_info("lmcache", "lmcache.lmcache_fs")
        defaults = runtime_defaults()
        backend = backend_info()
    report = {"schema": "lmcache_runtime_v1", "collected_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "Current collector process. Does not inspect or attest to an already running server's environment.",
              "python_executable": sys.executable, "python_version": sys.version,
              "lmcache_command": shutil.which("lmcache"), "PYTHONPATH": os.environ.get("PYTHONPATH"),
              "packages": packages, "native_fs": native_fs,
              "l1_config": defaults, "backend": backend,
              "collector_import_messages_tail": messages.getvalue()[-16000:],
              "version_note": "Metadata versions, source paths and native binary paths can differ. A .devN version does not uniquely identify a commit."}
    if args.base_path is not None:
        report["filesystem"] = filesystem_info(args.base_path)
    if args.server_log is not None:
        from inspect_store_log import inspect_log
        report["store_log"] = inspect_log(args.server_log, args.base_path)
    if args.output is not None:
        args.output.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = args.output / f"runtime_report_{stamp}_{os.getpid()}.json"
        report["report_file"] = str(target.resolve())
        with target.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, default=str)
            stream.write("\n")
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
