#!/usr/bin/env python3
"""Inspect existing LMCache logs and a bounded, top-level cache listing.

No requests, allocations, cache changes, or experiments are performed.
Classification describes only the observed log window, not the whole service.
"""

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SUBMIT = re.compile(r"Submitted store task\s+(\d+)\s+to adapter\s+(\d+)", re.I)
SUBMIT_SUMMARY = re.compile(r"L2 store submitted:\s*\d+\s+keys to adapter\s+(\d+)", re.I)
# In reviewed 240506 StoreController._finalize_store this exact DEBUG message
# is inside `if success`, unlike generic completion events/future messages.
CONFIRMED_TASK_SUCCESS = re.compile(r"L2 store task\s+(\d+)\s+completed:\s*adapter\s+(\d+)", re.I)
OBJECT_COUNTS = re.compile(r"L2 store completed:\s*adapter\s+(\d+),\s*(\d+)\s+succeeded,\s*(\d+)\s+failed", re.I)
FAILED = re.compile(r"(?:Store task\s+\d+\s+to adapter\s+\d+\s+failed|Failed to submit store task|FSL2Adapter (?:failed to store|store task .* failed))", re.I)
SKIP = re.compile(r"(?:SKIP:\s*\d+\s+tokens\s*<\s*chunk_size|\bno store tasks? (?:was |were )?submitted\b|\bno (?:new )?keys to store\b)", re.I)
DELETE = re.compile(r"(?:\bL2\b.*\b(?:delete|deletion) (?:completed|succeeded)\b|\bL2\b.*\bdeleted (?:key|keys)\b|\bFSL2Adapter deleted\b)", re.I)
NATIVE_ERROR = re.compile(r"O_DIRECT buffer address is not aligned|write failed:|open for write failed:|rename failed:|write returned 0|\bEFAULT\b|\bEINVAL\b|\bENOSPC\b|\bEACCES\b|Bad address|Invalid argument", re.I)
LAYOUT = re.compile(r"Engine KV Format:|KV shape:|PageBufferShapeDesc|\b(?:engine_kv_format|logical_size|physical_size|pad_to_physical|pad_buffers_to_alignment)\b", re.I)


def _keys(line, payload):
    value = payload.get("keys", payload.get("key")) if payload else None
    if value is None:
        objects = re.findall(r"ObjectKey\([^\n]*?\)", line[:8192])
        if objects:
            return objects
        match = re.search(r"\bkeys?\s*[:=]\s*(\[[^\]]*\]|'[^']*'|\"[^\"]*\")", line[:8192])
        if match:
            try:
                value = ast.literal_eval(match.group(1))
            except (ValueError, SyntaxError):
                return []
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [v if isinstance(v, str) else json.dumps(v, sort_keys=True, ensure_ascii=False) for v in values[:64]]


def _event(line, line_number):
    clean = ANSI.sub("", line)
    try:
        payload = json.loads(clean)
        if not isinstance(payload, dict):
            payload = {}
    except (ValueError, TypeError):
        payload = {}
    label = str(payload.get("event", payload.get("kind", ""))).lower()
    op = str(payload.get("op", payload.get("operation", ""))).lower()
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
    succeeded = payload.get("succeeded_count", metadata.get("succeeded_count"))
    failed = payload.get("failed_count", metadata.get("failed_count"))
    counted = OBJECT_COUNTS.search(clean)
    if counted:
        succeeded, failed = int(counted.group(2)), int(counted.group(3))
    has_counts = type(succeeded) is int and type(failed) is int and succeeded >= 0 and failed >= 0
    kind = None
    match = SUBMIT.search(clean)
    submit_summary = SUBMIT_SUMMARY.search(clean)
    done = CONFIRMED_TASK_SUCCESS.search(clean)
    if match or submit_summary or label in {"store_submitted", "l2_store_submitted"}:
        kind = "store_submitted"
    elif counted or label in {"store_completed", "l2_store_completed", "store_finished", "store_task_finished", "native_store_completed"}:
        if has_counts:
            if succeeded > 0 and failed > 0:
                kind = "store_partial"
            elif failed > 0:
                kind = "store_failed"
            elif succeeded > 0 and payload.get("ok") is not False:
                kind = "store_completed"
            else:
                kind = "task_finished"
        elif payload.get("ok") is True:
            kind = "store_completed"
        elif payload.get("ok") is False:
            kind = "store_failed"
        else:
            kind = "task_finished"
    elif done:
        kind = "store_completed"
    elif FAILED.search(clean) or label in {"store_failed", "l2_store_failed"}:
        kind = "store_failed"
    elif op in {"store", "set"} and payload.get("ok") is False:
        kind = "store_failed"
    elif DELETE.search(clean) or label in {"delete_completed", "l2_delete_completed"}:
        kind = "delete_completed" if payload.get("ok") is not False else "delete_failed"
    elif SKIP.search(clean) or label == "no_submit":
        kind = "explicit_no_submit"
    if kind is None:
        return None
    task = payload.get("task_id", metadata.get("task_id"))
    adapter = payload.get("adapter_id", payload.get("adapter_index", metadata.get("adapter_index")))
    if match or done:
        task, adapter = (match or done).groups()
    elif counted:
        adapter = counted.group(1)
    elif submit_summary:
        adapter = submit_summary.group(1)
    else:
        task_match = re.search(r"\btask(?:_id)?[=: ]+(\d+)", clean, re.I)
        adapter_match = re.search(r"\badapter(?:_id|_index)?[=: ]+(\d+)", clean, re.I)
        task = task if task is not None else (task_match.group(1) if task_match else None)
        adapter = adapter if adapter is not None else (adapter_match.group(1) if adapter_match else None)
    return {"kind": kind, "line_in_window": line_number, "task_id": str(task) if task is not None else None,
            "adapter_id": str(adapter) if adapter is not None else None,
            "future_id": payload.get("future_id"), "keys": _keys(clean, payload),
            "succeeded_count": succeeded if has_counts else None,
            "failed_count": failed if has_counts else None,
            "error": payload.get("error"), "raw": clean[:3000]}


def scan_cache(base_path, max_entries=1000, sample_files=12):
    report = {"path": str(base_path), "recursive": False, "max_entries": max_entries,
              "status": "unknown", "entries_observed": 0, "data_files_observed": 0,
              "data_bytes_observed": 0, "tmp_files_observed": 0, "limit_reached": False, "sample": []}
    try:
        with os.scandir(base_path) as entries:
            for index, entry in enumerate(entries):
                if index >= max_entries:
                    report["limit_reached"] = True
                    break
                report["entries_observed"] += 1
                if not entry.is_file(follow_symlinks=False):
                    continue
                is_data = entry.name.endswith(".data")
                is_tmp = entry.name.endswith(".tmp")
                if not (is_data or is_tmp):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if is_data:
                    report["data_files_observed"] += 1
                    report["data_bytes_observed"] += info.st_size
                else:
                    report["tmp_files_observed"] += 1
                if len(report["sample"]) < sample_files:
                    report["sample"].append({"name": entry.name, "bytes": info.st_size, "mtime_ns": info.st_mtime_ns})
        report["status"] = "bounded_sample" if report["limit_reached"] else "top_level_complete"
    except OSError as exc:
        report["error"] = str(exc)
    report["scope_note"] = "Observed top-level entries only; empty or partial listings do not prove a store failure."
    return report


def inspect_log(server_log, base_path=None, max_log_bytes=16 * 1024 * 1024, max_entries=1000, max_events=80):
    report = {"schema": "lmcache_store_log_v1", "classification": "unknown", "log": {"path": str(server_log)},
              "counts": {}, "events": [], "native_error_lines": [], "quota_evidence": [], "layout_evidence": [],
              "same_key_store_then_delete": [], "notes": []}
    counts = Counter()
    completed_keys = {}
    submitted_keys = {}
    unkeyed_completed = []
    try:
        with open(server_log, "rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            start = max(0, size - max_log_bytes)
            stream.seek(start)
            if start:
                stream.readline()
            data = stream.read(max_log_bytes)
        text = data.decode("utf-8", errors="replace")
        report["log"].update({"file_bytes_at_read": size, "window_start_byte": start,
                               "tail_truncated": start > 0, "bytes_read": len(data),
                               "utf8_replacement_characters": text.count("\ufffd")})
        for number, raw in enumerate(text.splitlines(), 1):
            clean = ANSI.sub("", raw)
            event = _event(clean, number)
            if event:
                counts[event["kind"]] += 1
                if event["succeeded_count"] is not None:
                    counts["reported_succeeded_objects_not_deduplicated"] += event["succeeded_count"]
                    counts["reported_failed_objects_not_deduplicated"] += event["failed_count"]
                task = (event["adapter_id"], event["task_id"])
                keys = event["keys"]
                if event["kind"] == "store_submitted" and task[1] is not None:
                    submitted_keys[task] = keys
                elif event["kind"] == "store_completed":
                    keys = keys or submitted_keys.get(task, [])
                    if not keys:
                        unkeyed_completed.append(number)
                    for key in keys:
                        completed_keys[key] = number
                elif event["kind"] == "delete_completed":
                    for key in keys:
                        if key in completed_keys and completed_keys[key] < number:
                            if len(report["same_key_store_then_delete"]) < max_events:
                                report["same_key_store_then_delete"].append({"key": key, "store_line": completed_keys[key], "delete_line": number})
                if len(report["events"]) < max_events:
                    report["events"].append(event)
            if NATIVE_ERROR.search(clean) or (event and event.get("error")):
                counts["native_error_lines"] += 1
                if len(report["native_error_lines"]) < max_events:
                    report["native_error_lines"].append({"line_in_window": number, "raw": clean[:3000]})
            if re.search(r"over quota|cache_salt=|/quota|\"limit_gb\"|\"current_usage_gb\"", clean, re.I):
                counts["quota_evidence_lines"] += 1
                if "over quota" in clean.lower():
                    counts["eviction_decisions"] += 1
                if len(report["quota_evidence"]) < max_events:
                    record = {"line_in_window": number, "raw": clean[:3000]}
                    for field in ("limit", "bytes", "watermark", "ratio"):
                        match = re.search(r"\b" + field + r"\s*=\s*([0-9.]+)", clean)
                        if match:
                            record[field] = match.group(1)
                    report["quota_evidence"].append(record)
            if LAYOUT.search(clean):
                counts["layout_evidence_lines"] += 1
                if len(report["layout_evidence"]) < max_events:
                    report["layout_evidence"].append({"line_in_window": number, "raw": clean[:3000]})
        success = counts["store_completed"] > 0 or counts["store_partial"] > 0
        failure = counts["store_failed"] > 0 or counts["store_partial"] > 0
        unbound_error = counts["native_error_lines"] > 0
        if success and (failure or unbound_error):
            report["classification"] = "mixed"
        elif success and report["same_key_store_then_delete"]:
            report["classification"] = "delete_after_store"
        elif failure:
            report["classification"] = "store_failed"
        elif success:
            report["classification"] = "store_completed"
        elif counts["explicit_no_submit"] and not counts["store_submitted"] and not unbound_error:
            report["classification"] = "no_submit"
        report["counts"] = dict(counts)
        report["notes"].extend([
            "Classification covers the observed log window only; task IDs are process-local and merged/restarted logs can be ambiguous.",
            "No failure line is not proof of success; no submit line is not proof of no submission.",
            "Store completion does not prove content correctness, an L2 readback, or actual O_DIRECT syscall flags.",
            "Generic completion without explicit ok/counters is task_finished, not confirmed success. The reviewed exact StoreController success-only DEBUG string is recognized separately.",
            "Counters count observed records, not distinct tasks or metric deltas; object count totals are explicitly not de-duplicated.",
            "Over-quota/evicting logs are decisions, not evidence that deletion completed.",
            "A failed batch can contain objects that were written before another object failed.",
        ])
        if unkeyed_completed:
            report["notes"].append("Some completed tasks lack key identities; do not correlate their later deletion by timing alone.")
        if success and unbound_error:
            report["notes"].append("Success and raw native error evidence coexist. They may belong to different tasks; mixed does not prove a specific successful task failed.")
    except OSError as exc:
        report["log"]["error"] = str(exc)
    if base_path is not None:
        report["cache_listing"] = scan_cache(base_path, max_entries=max_entries)
    return report


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-log", required=True, type=Path)
    parser.add_argument("--base-path", type=Path, help="Existing cache directory; top level only.")
    parser.add_argument("--max-log-bytes", type=positive, default=16 * 1024 * 1024)
    parser.add_argument("--max-entries", type=positive, default=1000)
    parser.add_argument("--max-events", type=positive, default=80)
    args = parser.parse_args()
    report = inspect_log(args.server_log, args.base_path, args.max_log_bytes, args.max_entries, args.max_events)
    report["collected_at_utc"] = datetime.now(timezone.utc).isoformat()
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
