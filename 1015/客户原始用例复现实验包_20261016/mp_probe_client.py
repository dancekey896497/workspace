#!/usr/bin/env python3
"""One synthetic fmt17 STORE through the real LMCache MP protocol.

No model, FakeStorageManager, production monkey patch, or replacement I/O
buffer is used. APIs were read at LMCache 240506edf5146613b015e9e2e51189f1fc099b76
and LMCache-Ascend b813e970075983e94e86cc2911a40b1c00e7c780. An incompatible
installed API is reported rather than silently changing the workload.

--help and --describe use only the standard library. Runtime requires an
already-working NPU stack and a separately started disposable MP server.
Exit 0 means this client's RPC/cleanup finished; it NEVER proves L2 persistence.
The runner must preserve this JSON plus the actual process exit status. An
abort after the pre-cleanup marker must not erase the earlier STORE evidence.
"""

from __future__ import annotations

import argparse
import importlib
import ipaddress
import inspect
import json
import os
from pathlib import Path
import sys
import time
import traceback
from urllib.parse import urlsplit
import uuid


MAIN_REF = "240506edf5146613b015e9e2e51189f1fc099b76"
ASCEND_REF = "b813e970075983e94e86cc2911a40b1c00e7c780"
PLANE_SPECS = ((512, "bfloat16"), (64, "bfloat16"), (128, "int8"), (1, "float16"))
LAYERS = 2
BLOCK_SIZE = 16


class InterfaceUnavailable(RuntimeError):
    """The installed stack cannot express this exact probe through its API."""


def describe() -> dict:
    return {
        "scope": "synthetic_fmt17_real_mp_NOT_customer_DeepSeek_layout",
        "source_refs": {"LMCache": MAIN_REF, "LMCache-Ascend": ASCEND_REF},
        "protocol": "REGISTER_KV_CACHE -> STORE -> UNREGISTER_KV_CACHE -> END_SESSION",
        "transfer_mode": "lmcache_driven",
        "chunk_size": 4096,
        "layers": LAYERS,
        "block_size": BLOCK_SIZE,
        "num_blocks": 4096 // BLOCK_SIZE,
        "plane_specs": [list(p) for p in PLANE_SPECS],
        "logical_object_bytes": LAYERS * 4096 * 1282,
        "real_server_required": True,
        "l2_proof": "Not supplied by client STORE ACK; use server completion/error and new files.",
        "l1_readback": "Optional --verify-l1; a hit may come from L1 and does not prove L2.",
        "cleanup_failure": "Retain IPC owners until the operator stops THIS test server and creates the stop marker.",
        "npu_runtime_validation": "NOT_VERIFIED on this development machine",
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--describe", action="store_true", help="Print the exact probe scope without importing torch.")
    p.add_argument("--rpc-url", default="tcp://127.0.0.1:15565")
    p.add_argument("--http-url", default="http://127.0.0.1:18090", help="Recorded for the independent server verifier; this client does not change HTTP state.")
    p.add_argument("--output", "--json-out", type=Path, help="New JSON evidence file; an existing file is never overwritten at startup.")
    p.add_argument("--sequence-id", type=int, default=None)
    p.add_argument("--instance-id", type=int, default=None)
    p.add_argument("--device-index", type=int, default=0)
    p.add_argument("--chunk-size", type=int, choices=(4096,), default=4096)
    p.add_argument("--timeout-seconds", type=float, default=60.0)
    p.add_argument("--hold-seconds", type=float, default=10.0, help="Keep registration/owners alive after STORE for server-side L2 observation.")
    p.add_argument("--cache-salt", default="")
    p.add_argument("--verify-l1", action="store_true")
    p.add_argument("--server-stopped-file", type=Path, help="Recovery handshake only: create AFTER this disposable server has exited.")
    p.add_argument("--server-pid-file", type=Path, help="Required identity evidence; default is output-directory/server.pid. Ports and L2 path are checked through /proc, without HTTP mutation.")
    return p


class Evidence:
    def __init__(self, path: Path, args: argparse.Namespace):
        self.path = path
        self.data = {
            **describe(),
            "pid": os.getpid(),
            "rpc_url": args.rpc_url,
            "http_url": args.http_url,
            "phase": "created",
            "outcome": "IN_PROGRESS",
            "store_rpc_completed": False,
            "store_ack": None,
            "l1_verified": None,
            "l2_verified": False,
            "cleanup_safe_to_release": True,
            "cleanup_completed": False,
            "pre_cleanup_marker": False,
            "markers": [],
            "cleanup_errors": [],
        }
        self.update("created")

    def update(self, phase: str, **fields) -> None:
        self.data.update(fields)
        self.data["phase"] = phase
        self.data["updated_unix_seconds"] = time.time()
        self.data["markers"].append({"phase": phase, "unix_seconds": time.time()})
        tmp = self.path.with_name(self.path.name + ".tmp." + str(os.getpid()))
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        print(json.dumps({"phase": phase, "evidence": str(self.path)}, ensure_ascii=False), flush=True)


def wait_result(future, timeout: float, operation: str):
    """Poll query() so a timeout is not hidden inside event.synchronize()."""
    if not callable(getattr(future, "query", None)) or not callable(getattr(future, "result", None)):
        raise InterfaceUnavailable(f"{operation}: future requires query() and result(timeout=...).")
    deadline = time.monotonic() + timeout
    while True:
        if future.query():
            return future.result(timeout=0)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{operation}: RPC/device event did not complete within {timeout:g}s")
        time.sleep(0.02)


def loopback_port(url: str, scheme: str) -> int:
    try:
        endpoint = urlsplit(url)
        address = ipaddress.ip_address(endpoint.hostname or "")
        port = endpoint.port
    except ValueError as exc:
        raise InterfaceUnavailable(f"Use a numeric loopback {scheme} URL with an explicit port: {url!r}") from exc
    if (endpoint.scheme != scheme or not address.is_loopback or port is None
            or endpoint.username is not None or endpoint.password is not None
            or endpoint.path not in ("", "/") or endpoint.query or endpoint.fragment):
        raise InterfaceUnavailable(f"Only a local numeric-loopback {scheme} test endpoint is permitted: {url!r}")
    return port


def validate_server_cmdline(argv: list[str], pid_path: Path, rpc_url: str, http_url: str) -> dict:
    """Pure comparison helper; no network requests or process mutations."""
    rpc_port = loopback_port(rpc_url, "tcp")
    http_port = loopback_port(http_url, "http")
    if "server" not in argv or not any(Path(arg).name == "lmcache" for arg in argv):
        raise InterfaceUnavailable("PID does not identify an lmcache server command")

    def values(option: str) -> list[str]:
        result = []
        for index, arg in enumerate(argv):
            if arg == option:
                if index + 1 >= len(argv):
                    raise InterfaceUnavailable(f"Server command is missing a value for {option}")
                result.append(argv[index + 1])
            elif arg.startswith(option + "="):
                result.append(arg[len(option) + 1:])
        return result

    for option, expected in (("--port", rpc_port), ("--http-port", http_port)):
        actual = values(option)
        if actual != [str(expected)]:
            raise InterfaceUnavailable(f"Test server identity mismatch: {option}={actual!r}, expected {expected}")
    for option, url in (("--host", rpc_url), ("--http-host", http_url)):
        actual = values(option)
        if actual != [urlsplit(url).hostname]:
            raise InterfaceUnavailable(f"Test server bind address mismatch: {option}={actual!r}")
    adapters = values("--l2-adapter")
    if len(adapters) != 1:
        raise InterfaceUnavailable("This probe requires exactly one explicit fs_native --l2-adapter")
    try:
        adapter = json.loads(adapters[0])
    except (ValueError, TypeError) as exc:
        raise InterfaceUnavailable(f"Test server L2 JSON cannot be read: {exc}") from exc
    expected_base = (pid_path.parent / "cache").resolve()
    if (not isinstance(adapter, dict) or adapter.get("type") != "fs_native"
            or not isinstance(adapter.get("base_path"), str)
            or not Path(adapter["base_path"]).is_absolute()
            or Path(adapter["base_path"]).resolve() != expected_base):
        raise InterfaceUnavailable(f"Test server fs_native base_path must equal this run's {expected_base}")
    return {"rpc_port": rpc_port, "http_port": http_port, "base_path": str(expected_base)}


def verify_test_server(args, output_path: Path) -> dict:
    pid_path = (args.server_pid_file or output_path.parent / "server.pid").resolve()
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
        if not text.isdecimal() or int(text) <= 0:
            raise ValueError("server.pid must contain one positive PID")
        pid = int(text)
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
        argv = [part.decode("utf-8") for part in raw.split(b"\0") if part]
    except (OSError, ValueError) as exc:
        raise InterfaceUnavailable(f"Cannot verify this run's server PID/cmdline from {pid_path}: {exc}") from exc
    checked = validate_server_cmdline(argv, pid_path, args.rpc_url, args.http_url)
    return {**checked, "pid": pid, "pid_file": str(pid_path), "verified_via": "/proc/PID/cmdline"}


def load_api() -> dict:
    """Import only the verified public path; no API-name guessing/fallback patch."""
    try:
        torch = importlib.import_module("torch")
        importlib.import_module("torch_npu")
        ascend = importlib.import_module("lmcache_ascend")
        lmcache = importlib.import_module("lmcache")
        zmq = importlib.import_module("zmq")
        factory = importlib.import_module("lmcache.v1.multiprocess.transport.factory")
        worker = importlib.import_module("lmcache.v1.multiprocess.transfer_context.worker_transfer")
        custom = importlib.import_module("lmcache.v1.multiprocess.custom_types")
        group = importlib.import_module("lmcache.v1.multiprocess.group_view")
        detection = importlib.import_module("lmcache.v1.gpu_connector.kv_format.detection")
        utils = importlib.import_module("lmcache.utils")
        native = importlib.import_module("lmcache.lmcache_native")
        api = {
            "torch": torch, "zmq": zmq, "lmcache": lmcache, "ascend": ascend,
            "factory": factory.RequestClientFactory,
            "context_cls": worker.LMCacheDrivenTransferContext,
            "key_cls": custom.IPCCacheServerKey,
            "group_cls": group.EngineGroupInfo,
            "detect_format": detection.detect_format,
            "engine_type": utils.EngineType.VLLM,
            "expected_format": native.EngineKVFormat.NL_X_NP_X_NB_BS_ONE_HS,
        }
    except (ImportError, AttributeError) as exc:
        raise InterfaceUnavailable(f"Installed API differs from the documented probe: {type(exc).__name__}: {exc}") from exc
    return api


def retain_event(future, event) -> None:
    # Keep our own event list as well. This mirrors the benchmark's raw-future
    # retention, without releasing it on an RPC timeout.
    raw = getattr(future, "raw_future_", future)
    if callable(getattr(raw, "retain_reference", None)):
        raw.retain_reference(event)


def hold_owners_until_server_stopped(args, report: Evidence) -> None:
    pid_path = args.server_pid_file or report.path.parent / "server.pid"
    try:
        server_pid = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        server_pid = "UNKNOWN (use only this run's server PID)"
    marker = args.server_stopped_file
    report.update(
        "WAITING_FOR_TEST_SERVER_STOP",
        cleanup_safe_to_release=False,
        recovery_server_pid=server_pid,
        recovery_marker=str(marker) if marker else None,
        recovery_instruction="Stop ONLY this run's disposable server, confirm exit, then create server-stopped marker. Owners remain held; no repeated STORE requests.",
    )
    print(f"OWNERS_RETAINED: stop this run's test server PID={server_pid}; marker={marker}. Do not kill the client first.", flush=True)
    # Deliberately fail closed: a request timeout is not cancellation. There is
    # no safe automatic deadline for dropping IPC-owned memory on an unknown
    # server. The operator handshake ends this recovery wait, not a retry loop.
    while marker is None or not marker.is_file():
        try:
            time.sleep(0.5)
        except KeyboardInterrupt:
            print("OWNERS_RETAINED: server-stop confirmation is still required.", flush=True)
    report.update("test_server_stop_confirmed", cleanup_safe_to_release=True)


def verify_l1(api, request_client, context, key, caches, golden, owners, args, report):
    for name in ("lookup", "query_prefetch_status"):
        if not callable(getattr(request_client, name, None)):
            raise InterfaceUnavailable(f"Optional L1 verification requires RequestClient.{name}")
    lookup_key = key.no_worker_id_version()
    wait_result(request_client.lookup(lookup_key, 1), args.timeout_seconds, "LOOKUP")
    deadline = time.monotonic() + args.timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PREFETCH status did not settle within the timeout")
        hits = wait_result(request_client.query_prefetch_status(key.request_id), remaining, "PREFETCH_STATUS")
        if hits is not None:
            break
        time.sleep(0.02)
    if int(hits) < 1:
        raise RuntimeError(f"Optional L1 readback had no full chunk: hit_chunks={hits}")
    for planes in caches.values():
        for plane in planes:
            plane.zero_()
    event = context.create_recorded_event()
    owners["events"].append(event)
    transfer = {"operation": "RETRIEVE", "submission_attempted": True, "submitted": False, "settled": False}
    owners["transfers"].append(transfer)
    future = context.submit_retrieve(key.request_id, key, caches, [list(range(args.chunk_size // BLOCK_SIZE))], event, args.chunk_size // BLOCK_SIZE)
    transfer["submitted"] = True
    owners["futures"].append(future)
    retain_event(future, event)
    reply = wait_result(future, args.timeout_seconds, "RETRIEVE")
    transfer["settled"] = True
    if reply is not True:
        raise RuntimeError(f"RETRIEVE returned {reply!r}")
    torch = api["torch"]
    checks = []
    for name, planes in caches.items():
        for index, plane in enumerate(planes):
            checks.append({"layer": name, "plane": index, "equal": bool(torch.equal(plane.cpu(), golden[name][index]))})
    equal = all(item["equal"] for item in checks)
    report.update("l1_readback_checked", l1_verified=equal, l1_checks=checks, l1_scope="May hit L1; not a disk-reload/persistence test.")
    if not equal:
        raise RuntimeError("L1 round-trip content mismatch")


def run(args, report: Evidence) -> int:
    api = None
    zmq_context = request_client = context = None
    registration_attempted = False
    owners = {"planes": {}, "golden": {}, "events": [], "futures": [], "transfers": []}
    failed = False
    try:
        report.update("verifying_test_server")
        report.update("server_identity_verified", server_identity=verify_test_server(args, report.path))
        report.update("importing_runtime")
        api = load_api()
        torch = api["torch"]
        if not hasattr(torch, "npu") or not torch.npu.is_available():
            raise InterfaceUnavailable("NPU runtime is unavailable; no CPU fallback is permitted.")
        torch.npu.set_device(args.device_index)
        device = torch.device(f"npu:{args.device_index}")
        zmq_context = api["zmq"].Context()
        zmq_context.setsockopt(api["zmq"].LINGER, 0)
        request_client = api["factory"].create(args.rpc_url, context=zmq_context)
        server_chunk = int(wait_result(request_client.get_chunk_size(), args.timeout_seconds, "GET_CHUNK_SIZE"))
        if server_chunk != args.chunk_size:
            raise InterfaceUnavailable(f"Server chunk={server_chunk}, required={args.chunk_size}; do not change the business configuration to force a pass.")
        nb = args.chunk_size // BLOCK_SIZE
        plane_metadata = []
        for layer in range(LAYERS):
            planes, expected = [], []
            for index, (width, dtype_name) in enumerate(PLANE_SPECS):
                shape = (nb, BLOCK_SIZE, 1, width)
                cpu = ((torch.arange(nb * BLOCK_SIZE * width, dtype=torch.int32) % 31) + layer * 7 + index * 3).to(getattr(torch, dtype_name)).reshape(shape)
                plane = cpu.to(device)
                planes.append(plane)
                expected.append(cpu)
                plane_metadata.append({"layer": layer, "plane": index, "shape": list(shape), "dtype": dtype_name, "stride": list(plane.stride()), "storage_offset_elements": int(plane.storage_offset()), "data_ptr": hex(plane.data_ptr())})
            name = f"layer.{layer}"
            owners["planes"][name] = tuple(planes)
            owners["golden"][name] = tuple(expected)
        caches = owners["planes"]
        try:
            fmt, normalized = api["detect_format"](list(caches.values()), api["engine_type"], {"kv_layout": "NHD"})
        except (ValueError, TypeError, AttributeError) as exc:
            raise InterfaceUnavailable(f"Installed format detector cannot describe this plane tuple: {exc}") from exc
        if int(fmt) != int(api["expected_format"]):
            raise InterfaceUnavailable(f"Installed detector returned {fmt!s}; this probe requires fmt17.")
        caches = {f"layer.{i}": value for i, value in enumerate(normalized)}
        owners["planes"] = caches
        groups = [api["group_cls"](engine_group_id=0, layer_indices=tuple(range(LAYERS)), tokens_per_block=BLOCK_SIZE)]
        context = api["context_cls"](args.instance_id, request_client)
        register_kwargs = {"layout_hints": {"kv_layout": "NHD"}, "engine_group_infos": groups, "engine_type": api["engine_type"]}
        try:
            inspect.signature(context.register).bind(caches, args.model_name, 1, nb, args.timeout_seconds, **register_kwargs)
        except (TypeError, ValueError) as exc:
            raise InterfaceUnavailable(f"REGISTER signature differs: {exc}") from exc
        # Recheck immediately before the first mutating RPC after allocation;
        # importing the runtime must not turn a stale PID into authorization.
        report.update("server_identity_rechecked", server_identity=verify_test_server(args, report.path))
        report.update("registering", server_chunk_size=server_chunk, model_name=args.model_name, filename_model_marker=args.model_name, request_id=args.request_id, sequence_id=args.sequence_id, instance_id=args.instance_id, cache_salt=args.cache_salt, client_detected_format={"value": int(fmt), "name": str(fmt)}, planes=plane_metadata, runtime_paths={"lmcache": api["lmcache"].__file__, "lmcache_ascend": api["ascend"].__file__, "transfer_context": inspect.getfile(api["context_cls"])}, cleanup_safe_to_release=False)
        registration_attempted = True
        context.register(caches, args.model_name, 1, nb, args.timeout_seconds, **register_kwargs)
        key = api["key_cls"](model_name=args.model_name, world_size=1, worker_id=0, token_ids=(args.sequence_id,) + (12345,) * (args.chunk_size - 1), start=0, end=args.chunk_size, request_id=args.request_id, cache_salt=args.cache_salt, num_kv_readers=1)
        event = context.create_recorded_event()
        owners["events"].append(event)
        report.update("store_submitting", registration_ack=True, block_ids={"first": 0, "last": nb - 1, "count": nb}, expected_min_l2_objects=1, l2_evidence_required={"model_filename_contains": args.model_name, "request_id": args.request_id, "need": "server L2 store completion/error plus new final files/bytes; STORE ACK is insufficient"})
        started = time.monotonic()
        transfer = {"operation": "STORE", "submission_attempted": True, "submitted": False, "settled": False}
        owners["transfers"].append(transfer)
        future = context.submit_store(args.request_id, key, caches, [list(range(nb))], event, nb)
        transfer["submitted"] = True
        owners["futures"].append(future)
        retain_event(future, event)
        reply = wait_result(future, args.timeout_seconds, "STORE")
        transfer["settled"] = True
        report.update("store_rpc_completed", store_rpc_completed=True, store_ack=reply, store_device_completion=True, store_elapsed_seconds=time.monotonic() - started, outcome="STORE_ACK_L2_UNVERIFIED" if reply is True else "STORE_REJECTED")
        if reply is not True:
            raise RuntimeError(f"STORE returned {reply!r}")
        report.update("observing_server_l2", hold_seconds=args.hold_seconds)
        time.sleep(args.hold_seconds)
        if args.verify_l1:
            verify_l1(api, request_client, context, key, caches, owners["golden"], owners, args, report)
    except (InterfaceUnavailable, NotImplementedError) as exc:
        failed = True
        report.update("interface_unavailable", outcome="SKIP_INTERFACE_DIFFERENCE", error=f"{type(exc).__name__}: {exc}")
    except BaseException as exc:
        failed = True
        report.update("probe_failed", outcome="FAILED", error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    finally:
        # Flush durable evidence BEFORE unregister/driver cleanup. The shell
        # runner records a later exit 134 separately without erasing this file.
        unsettled = [dict(item) for item in owners["transfers"] if not item["settled"]]
        report.update("pre_cleanup", pre_cleanup_marker=True, cleanup_completed=False, transfer_states=owners["transfers"], unsettled_transfers=unsettled, store_evidence_before_cleanup={"store_rpc_completed": report.data["store_rpc_completed"], "store_ack": report.data["store_ack"], "l1_verified": report.data["l1_verified"], "l2_verified": False, "outcome": report.data["outcome"]})
        safe = not registration_attempted
        if unsettled:
            # An ACK from UNREGISTER cannot prove a queued blocking transfer
            # never launches later. Do not send cleanup RPCs as a substitute
            # for confirmed completion. Keep every IPC owner until server exit.
            failed = True
            report.data["cleanup_errors"].append("STORE/RETRIEVE completion is unconfirmed; cleanup RPCs skipped until this test server stops")
            hold_owners_until_server_stopped(args, report)
            safe = True
        elif registration_attempted and context is not None:
            try:
                # Only confirmed-settled transfers use the normal cleanup
                # protocol. Plane owners and events remain held until its ACK.
                unreg = context.unregister()
                if unreg is not None:
                    wait_result(unreg, args.timeout_seconds, "UNREGISTER_KV_CACHE")
                # The audited TransferContext returns None only if REGISTER
                # was never submitted (e.g. a local event-support check failed).
                safe = True
                report.update("unregister_finished", unregister_ack=unreg is not None, unregister_not_needed=unreg is None, cleanup_safe_to_release=True)
                try:
                    wait_result(request_client.end_session(args.request_id), args.timeout_seconds, "END_SESSION")
                except Exception as exc:
                    failed = True
                    report.data["cleanup_errors"].append(f"END_SESSION: {type(exc).__name__}: {exc}")
            except BaseException as exc:
                failed = True
                report.data["cleanup_errors"].append(f"UNREGISTER: {type(exc).__name__}: {exc}")
        if not safe:
            hold_owners_until_server_stopped(args, report)
        for label, obj in (("transfer_context", context), ("request_client", request_client)):
            if obj is not None:
                try:
                    obj.close()
                except Exception as exc:
                    failed = True
                    report.data["cleanup_errors"].append(f"{label}.close: {type(exc).__name__}: {exc}")
        # Keep owners through remote context close. Do not add another device
        # operation here just to investigate the known interpreter-exit side path.
        if zmq_context is not None:
            try:
                zmq_context.term()
            except Exception as exc:
                failed = True
                report.data["cleanup_errors"].append(f"zmq.term: {type(exc).__name__}: {exc}")
        report.update("cleanup_finished", cleanup_completed=not bool(report.data["cleanup_errors"]), cleanup_safe_to_release=True, client_exit_code=2 if failed else 0, process_exit_status="External runner must capture actual status; later native abort is still possible.")
        owners.clear()
    return 2 if failed else 0


def main() -> int:
    p = parser()
    args = p.parse_args()
    if args.describe:
        print(json.dumps(describe(), ensure_ascii=False, indent=2))
        return 0
    if args.output is None:
        p.error("--output is required for an actual run")
    if args.timeout_seconds <= 0 or args.hold_seconds < 0 or args.device_index < 0:
        p.error("timeout must be positive; hold/device-index must be non-negative")
    if args.sequence_id is None:
        args.sequence_id = uuid.uuid4().int % (1 << 30)
    if not 0 <= args.sequence_id < (1 << 31):
        p.error("--sequence-id must be in [0, 2**31)")
    if args.instance_id is None:
        args.instance_id = 100000 + os.getpid()
    if args.instance_id < 0:
        p.error("--instance-id must be non-negative")
    args.output = args.output.resolve()
    if args.output.exists():
        p.error("--output already exists; use a new evidence file")
    if args.server_stopped_file is not None:
        args.server_stopped_file = args.server_stopped_file.resolve()
        if args.server_stopped_file.exists():
            p.error("server-stopped marker already exists; use this run's new marker path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:12]
    args.model_name = "npu-fmt17-probe-" + run_id
    args.request_id = "npu-fmt17-" + run_id + "-" + str(args.sequence_id)
    report = Evidence(args.output, args)
    return run(args, report)


if __name__ == "__main__":
    raise SystemExit(main())
