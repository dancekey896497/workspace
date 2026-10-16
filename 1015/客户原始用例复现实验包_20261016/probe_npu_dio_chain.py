#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""One format-17 NPU -> real Lazy L1 -> native FS diagnostic, without a model.

Only the new private run directory is written. No package/source is patched.
--help and --describe require only Python's standard library.
"""

import argparse
import ctypes
import inspect
import json
import math
import os
from pathlib import Path
import select
import signal
import sys
import tempfile
import time
import traceback
import uuid


NL, NB, BS, SLOTS, ALIGN = 2, 4, 16, 64, 4096
PLANE_SPECS = ((512, "bfloat16", 2), (64, "bfloat16", 2),
               (128, "int8", 1), (1, "float16", 2))
ROW_BYTES = sum(width * size for width, _, size in PLANE_SPECS)
LOGICAL = NL * SLOTS * ROW_BYTES
PHYSICAL = (LOGICAL + ALIGN - 1) // ALIGN * ALIGN
PAD = 61
MAIN_REF = "240506edf5146613b015e9e2e51189f1fc099b76"
ASCEND_REF = "b813e970075983e94e86cc2911a40b1c00e7c780"


class ProbeFailure(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def description():
    return {
        "case": "dsa_c8_4_small_format17",
        "layers": NL, "blocks": NB, "block_size": BS, "actual_slots": SLOTS,
        "planes": [{"width": w, "dtype": d, "itemsize": n}
                   for w, d, n in PLANE_SPECS],
        "slot_bytes": ROW_BYTES, "logical_bytes": LOGICAL,
        "align_bytes": ALIGN, "physical_bytes": PHYSICAL,
        "padding_bytes": PHYSICAL - LOGICAL,
        "steps": ["real Lazy allocation", "CPU-filled same-object FS write/read",
                  "native format-17 D2H into same object on a dedicated stream",
                  "wait for that stream", "verify logical content and padding",
                  "same-object FS write/read with a new key"],
        "default_view": "actual installed adapter helper; length fallback is not DIO PASS",
        "reference_option": "--padded-view-reference uses a test-local original-address P-byte view",
        "scope": "component only; no model, IPC, MP server, quota or core analysis",
        "references": [
            f"https://github.com/LMCache/LMCache-Ascend/blob/{ASCEND_REF}/tests/v1/test_mp_block_transfer.py#L303",
            f"https://github.com/LMCache/LMCache-Ascend/blob/{ASCEND_REF}/tests/v1/test_mp_block_transfer.py#L108",
            f"https://github.com/LMCache/LMCache/blob/{MAIN_REF}/lmcache/v1/gpu_connector/utils.py#L672",
        ],
    }


def callable_info(fn):
    try:
        path = inspect.getfile(fn)
    except (TypeError, OSError):
        path = None
    try:
        signature = str(inspect.signature(fn))
    except (TypeError, ValueError):
        signature = None
    return {"module": getattr(fn, "__module__", None),
            "qualname": getattr(fn, "__qualname__", None),
            "type": type(fn).__qualname__, "file": path, "signature": signature}


class Reporter:
    def __init__(self, path, data):
        self.path, self.data = path, data

    def save(self, phase=None):
        if phase is not None:
            self.data["phase"] = phase
            self.data.setdefault("phase_history", []).append(phase)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        temporary.replace(self.path)


def pointer(view):
    return ctypes.addressof(ctypes.c_ubyte.from_buffer(view))


def wait_completion(client, future_id, timeout):
    end = time.monotonic() + timeout
    while True:
        for item in client.drain_completions():
            if len(item) != 4 or int(item[0]) != future_id:
                raise ProbeFailure("NATIVE_COMPLETION_CONTRACT_MISMATCH", repr(item))
            return {"future_id": int(item[0]), "ok": bool(item[1]),
                    "error": str(item[2]), "result_bools": item[3]}
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise ProbeFailure("NATIVE_COMPLETION_TIMEOUT", str(future_id))
        select.select([client.event_fd()], [], [], min(remaining, 0.1))


def confirm_stream_before_release(stream, reporter):
    """Keep the caller's owners alive until this exact stream is confirmed idle."""
    report = reporter.data
    try:
        stream.synchronize()
    except BaseException as exc:
        original = f"{type(exc).__name__}: {exc}"
        report.update(status="NPU_COMPLETION_UNKNOWN_OWNERS_RETAINED",
                      phase="NPU_COMPLETION_UNKNOWN_OWNERS_RETAINED",
                      cleanup_safe_to_release=False,
                      original_cleanup_sync_error=original,
                      original_cleanup_sync_traceback=traceback.format_exc(),
                      cleanup_sync_retry_count=0,
                      retention_pid=os.getpid(),
                      recovery="Keep this process alive; retry only the same stream synchronization every 5 seconds. No business replay or device reset.")
        # Ctrl-C must not unwind the caller and destroy its in-flight owners.
        previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            try:
                print("NPU completion unknown: owners retained; same-stream synchronization "
                      "will be retried every 5 seconds. Ctrl-C is ignored while unsafe. "
                      f"Original error: {original}", file=sys.stderr, flush=True)
            except BaseException:
                pass
            while True:
                try:
                    reporter.save()
                except BaseException as report_exc:
                    # A report-write error must not release an in-flight DMA buffer.
                    report["retention_report_write_error"] = repr(report_exc)
                try:
                    time.sleep(5)
                except BaseException:
                    continue
                report["cleanup_sync_retry_count"] += 1
                try:
                    stream.synchronize()
                except BaseException as retry_exc:
                    report["last_cleanup_sync_error"] = repr(retry_exc)
                    continue
                report.update(cleanup_safe_to_release=True,
                              status="NPU_COMPLETION_RECOVERED_AFTER_ERROR")
                return ["NPU stream synchronization: " + original]
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
    else:
        report["cleanup_safe_to_release"] = True
        return []


def object_info(obj, view, base, pool_bytes, disk_block):
    p, q, offset = int(obj.data_ptr), pointer(view), int(obj.meta.address)
    physical, logical = int(obj.get_physical_size()), int(obj.get_size())
    return {
        "identity": id(obj), "type": type(obj).__qualname__,
        "B_pool_base": base, "o_pool_offset": offset, "p_object": p, "q_view": q,
        "p_hex": hex(p), "q_hex": hex(q), "p_equals_B_plus_o": p == base + offset,
        "same_pointer": p == q, "L_logical": logical, "P_physical": physical,
        "raw_tensor_bytes": int(obj.raw_tensor.nbytes), "view_bytes": view.nbytes,
        "submitted_bytes": view.nbytes, "itemsize": view.itemsize,
        "B_mod_D": base % disk_block, "o_mod_D": offset % disk_block,
        "p_mod_D": p % disk_block, "q_mod_D": q % disk_block,
        "length_mod_D": view.nbytes % disk_block,
        "slot_within_pool": 0 <= offset and offset + physical <= pool_bytes,
        "view_within_slot": p <= q and q + view.nbytes <= p + physical,
        "direct_parameters_satisfied": (hasattr(os, "O_DIRECT")
                                        and q % disk_block == 0
                                        and view.nbytes % disk_block == 0),
    }


def make_config(config_type, report):
    values = dict(size_in_bytes=64 << 20, use_lazy=True,
                  init_size_in_bytes=64 << 20, align_bytes=ALIGN, shm_name="")
    signature = inspect.signature(config_type)
    missing = [name for name in values if name not in signature.parameters]
    if missing:
        raise ProbeFailure("L1_CONFIG_INTERFACE_UNSUPPORTED", repr(missing))
    optional = {name: False for name in ("use_hugepages", "mlock")
                if name in signature.parameters}
    report["config_interface"] = {"signature": str(signature),
                                  "arguments": {**values, **optional},
                                  "omitted_optional": [n for n in ("use_hugepages", "mlock")
                                                       if n not in optional]}
    cfg = config_type(**values, **optional)
    if not cfg.use_lazy:
        raise ProbeFailure("SKIP_LAZY_AUTO_DISABLED", "installed backend disabled Lazy")
    return cfg


def adapter_view(helper, obj, reference, held, report):
    signature = inspect.signature(helper)
    try:
        signature.bind(obj, True)
        supports_padding = True
    except TypeError:
        signature.bind(obj)
        supports_padding = False
    actual = helper(obj, True) if supports_padding else helper(obj)
    held.append(actual)
    report["view_contract"] = {
        "actual_helper": callable_info(helper), "supports_padding_argument": supports_padding,
        "actual_adapter_bytes": actual.nbytes,
        "selected": "TEST_LOCAL_PADDED_REFERENCE" if reference else "ACTUAL_ADAPTER_VIEW",
        "actual_adapter_tested": not reference,
    }
    if not reference:
        return actual
    # This overlays the allocated slot. It never rounds/moves the pointer.
    array = (ctypes.c_ubyte * int(obj.get_physical_size())).from_address(int(obj.data_ptr))
    view = memoryview(array)
    held.append(view)
    return view


def check_native_failure(completion, stage):
    if completion["ok"]:
        return
    error = completion["error"]
    if "O_DIRECT buffer address is not aligned" in error:
        status = "ADDRESS_GUARD_BEFORE_IO"
    elif "write failed:" in error:
        status = "WRITE_SYSCALL_FAILED"
    else:
        status = "NATIVE_FS_FAILED"
    raise ProbeFailure(status, f"{stage}: {error}")


def run(args, reporter):
    report = reporter.data
    allocator = client = stream = None
    held_objects, held_views, owners = [], [], []
    try:
        reporter.save("IMPORT_AND_NPU_SETUP")
        import torch
        import torch_npu
        torch.npu.set_device(args.device_index)
        import lmcache
        import lmcache_ascend
        import lmcache_ascend.c_ops as ascend_native
        import lmcache.lmcache_fs as fs_native
        import lmcache.lmcache_native as native
        from lmcache.utils import EngineType
        from lmcache.v1.distributed.config import L1MemoryManagerConfig
        from lmcache.v1.distributed.memory_manager.l1_memory_manager import create_memory_allocator
        from lmcache.v1.distributed.l2_adapters.native_connector_l2_adapter import _obj_to_memoryview
        from lmcache.v1.memory_management import MemoryFormat
        from lmcache.v1.gpu_connector import utils as kv_utils

        report["environment"] = {
            "torch": str(torch.__version__), "torch_npu": str(getattr(torch_npu, "__version__", "unknown")),
            "torch_device_type": str(lmcache.torch_device_type),
            "lmcache": str(lmcache.__file__), "ascend": str(lmcache_ascend.__file__),
            "ascend_native": str(ascend_native.__file__), "native_fs": str(fs_native.__file__),
            "allocator_factory": callable_info(create_memory_allocator),
            "native_transfer": callable_info(ascend_native.multi_layer_block_kv_transfer),
        }
        if str(lmcache.torch_device_type) != "npu":
            raise ProbeFailure("NPU_BACKEND_NOT_SELECTED", str(lmcache.torch_device_type))
        directory = Path(report["run_directory"])
        disk_block = int(os.statvfs(directory).f_bsize)
        if disk_block <= 0:
            raise ProbeFailure("FILESYSTEM_BLOCK_SIZE_UNKNOWN", str(disk_block))
        report["filesystem"] = {"block_size_D": disk_block, "configured_use_odirect": True,
                                "actual_odirect_syscall": "NOT_VERIFIED_NO_TRACE"}
        cfg = make_config(L1MemoryManagerConfig, report)
        allocator = create_memory_allocator(cfg)
        report["allocator"] = {"class": type(allocator).__qualname__,
                               "init": callable_info(type(allocator).__init__)}
        if type(allocator).__name__ != "LazyMemoryAllocator":
            raise ProbeFailure("NOT_REAL_LAZY_ALLOCATOR", type(allocator).__qualname__)
        pool = allocator.get_underlying_buffer()
        base, pool_bytes = int(pool.data_ptr()), int(pool.nbytes)
        report["allocator"].update(pool_base=base, actual_pool_bytes=pool_bytes)
        objects = allocator.batched_allocate(torch.Size([1, NL, SLOTS, ROW_BYTES]), torch.uint8,
                                             2, fmt=MemoryFormat.KV_MLA_FMT)
        if objects is None or len(objects) != 2:
            raise ProbeFailure("L1_ALLOCATION_FAILED", "source and readback objects required")
        held_objects.extend(objects)
        source, destination = objects
        for obj in objects:
            o, p, physical = int(obj.meta.address), int(obj.data_ptr), int(obj.get_physical_size())
            if not (p == base + o and 0 <= o and o + physical <= pool_bytes
                    and int(obj.get_size()) == LOGICAL and physical == PHYSICAL):
                raise ProbeFailure("L1_SLOT_CONTRACT_MISMATCH", repr((o, p, physical)))
        physical_views = []
        physical_tensors = []
        for obj in objects:
            view = memoryview((ctypes.c_ubyte * PHYSICAL).from_address(int(obj.data_ptr)))
            held_views.append(view)
            physical_views.append(view)
            physical_tensors.append(torch.frombuffer(view, dtype=torch.uint8))
        owners.extend([pool, objects, physical_views, physical_tensors])
        source_view = adapter_view(_obj_to_memoryview, source, args.padded_view_reference,
                                   held_views, report)
        destination_view = adapter_view(_obj_to_memoryview, destination, args.padded_view_reference,
                                        held_views, report)
        for obj, view in zip(objects, (source_view, destination_view)):
            info = object_info(obj, view, base, pool_bytes, disk_block)
            if not (info["same_pointer"] and info["view_within_slot"] and view.itemsize == 1
                    and LOGICAL <= view.nbytes <= PHYSICAL):
                raise ProbeFailure("ADAPTER_VIEW_CONTRACT_MISMATCH", repr(info))
        if source_view.nbytes != destination_view.nbytes:
            raise ProbeFailure("ADAPTER_VIEW_CONTRACT_MISMATCH", "source/readback lengths differ")
        report["source_before"] = object_info(source, source_view, base, pool_bytes, disk_block)
        report["destination"] = object_info(destination, destination_view, base, pool_bytes, disk_block)
        client = fs_native.LMCacheFSClient(str(directory), 2, "", True, 0)

        def fs_phase(name, expected):
            phase = {"key": f"npu_chain_{report['run_id']}@00000000@0@{uuid.uuid4().hex}",
                     "source": object_info(source, source_view, base, pool_bytes, disk_block)}
            report["fs_phases"][name] = phase
            reporter.save(name + "_STORE")
            fid = int(client.submit_batch_set([phase["key"]], [source_view]))
            phase["store"] = wait_completion(client, fid, args.timeout)
            reporter.save()
            check_native_failure(phase["store"], name + " store")
            physical_tensors[1].zero_()
            reporter.save(name + "_LOAD")
            fid = int(client.submit_batch_get([phase["key"]], [destination_view]))
            phase["load"] = wait_completion(client, fid, args.timeout)
            reporter.save()
            check_native_failure(phase["load"], name + " load")
            if phase["load"]["result_bools"] != [True]:
                raise ProbeFailure("NATIVE_GET_KEY_FAILED", repr(phase["load"]))
            count = source_view.nbytes
            phase["readback_equal"] = bool(torch.equal(physical_tensors[1][:count], expected[:count]))
            phase["logical_readback_equal"] = bool(torch.equal(physical_tensors[1][:LOGICAL], expected[:LOGICAL]))
            phase["direct_parameters_satisfied"] = bool(
                phase["source"]["direct_parameters_satisfied"]
                and report["destination"]["direct_parameters_satisfied"])
            phase["io_label"] = ("DIRECT_PARAMETERS_MET_SYSCALL_NOT_VERIFIED"
                                 if phase["direct_parameters_satisfied"]
                                 else "BUFFERED_FALLBACK_LENGTH_OR_PLATFORM")
            reporter.save()
            if not phase["readback_equal"] or not phase["logical_readback_equal"]:
                raise ProbeFailure("FS_READBACK_CONTENT_MISMATCH", name)

        physical_tensors[0].fill_(PAD)
        expected_before = torch.full((PHYSICAL,), PAD, dtype=torch.uint8)
        owners.append(expected_before)
        fs_phase("CPU_BEFORE", expected_before)

        reporter.save("BUILD_FORMAT17")
        device = torch.device(f"npu:{args.device_index}")
        stream = torch.npu.Stream(device=device)
        layers, expected_rows, cpu_planes = [], [], []
        with torch.npu.stream(stream):
            for layer_index in range(NL):
                row, planes = [], []
                for plane_index, (width, dtype_name, _) in enumerate(PLANE_SPECS):
                    dtype = getattr(torch, dtype_name)
                    values = (torch.arange(SLOTS * width, dtype=torch.int32)
                              + layer_index * 7 + plane_index * 11) % 31
                    typed = values.to(dtype).view(NB, BS, 1, width)
                    cpu_planes.append(typed)
                    row.append(typed.view(torch.uint8).reshape(SLOTS, -1))
                    planes.append(typed.to(device))
                layers.append(tuple(planes))
                expected_rows.append(torch.cat(row, dim=1))
            fmt, normalized = kv_utils.normalize_kv_and_discover_format(layers, EngineType.VLLM)
            expected_fmt = native.EngineKVFormat.NL_X_NP_X_NB_BS_ONE_HS
            if fmt != expected_fmt:
                raise ProbeFailure("FORMAT17_NOT_DETECTED", str(fmt))
            desc = kv_utils.make_page_buffer_shape_desc(normalized, fmt, 0, NL, NB, BS)
            report["format17"] = {
                "detected": str(fmt),
                "descriptor_factory": callable_info(kv_utils.make_page_buffer_shape_desc),
                "descriptor_class": type(desc).__qualname__,
                "scalar_geometry": {n: getattr(desc, n, None) for n in
                                    ("nl", "nb", "bs", "nh", "hs", "kv_size", "element_size")},
                "dtype": str(getattr(desc, "dtype", None)),
                "has_plane_geometry": hasattr(desc, "plane_slot_bytes"),
            }
            reporter.save()
            slots = tuple(int(x) for x in desc.plane_slot_bytes)
            strides = tuple(int(x) for x in desc.plane_block_stride_bytes)
            wanted_slots = tuple(w * n for w, _, n in PLANE_SPECS)
            if not (isinstance(desc, ascend_native.PageBufferShapeDesc)
                    and int(desc.num_planes) == 4 and slots == wanted_slots
                    and strides == tuple(BS * x for x in wanted_slots)
                    and int(desc.nl) == NL and int(desc.nb) == NB and int(desc.bs) == BS
                    and int(desc.kv_size) == 1 and int(desc.nh) == 1
                    and int(desc.hs) * int(desc.element_size) == ROW_BYTES):
                raise ProbeFailure("FORMAT17_DESCRIPTOR_MISMATCH", "native class or plane geometry differs")
            needed = int(desc.nl) * SLOTS * sum(slots)
            if needed != LOGICAL or needed > int(source.get_physical_size()):
                raise ProbeFailure("NPU_COPY_EXCEEDS_SLOT", str(needed))
            table = torch.tensor([int(t.data_ptr()) for layer in normalized for t in layer],
                                 dtype=torch.int64, device=device)
            block_ids = torch.arange(NB, dtype=torch.int64, device=device)
        owners.extend([layers, normalized, cpu_planes, expected_rows, desc, table, block_ids])
        expected_after = torch.full((PHYSICAL,), PAD, dtype=torch.uint8)
        expected_after[:LOGICAL].copy_(torch.stack(expected_rows).reshape(-1))
        owners.append(expected_after)
        stream.synchronize()
        report["format17"].update({
            "detected": str(fmt), "case": "official dsa_c8_4 small geometry; not customer DS layout",
            "descriptor_factory": callable_info(kv_utils.make_page_buffer_shape_desc),
            "descriptor_class": type(desc).__qualname__, "num_planes": int(desc.num_planes),
            "plane_slot_bytes": slots, "plane_block_stride_bytes": strides,
            "derived_native_object_bytes": needed, "slots_passed_to_native": SLOTS,
            "stream_pointer": int(stream.npu_stream),
            "owners_held": True, "manual_host_register_performed": False,
        })
        reporter.save("NPU_D2H_SUBMIT")
        started = time.monotonic()
        with torch.npu.stream(stream):
            ascend_native.multi_layer_block_kv_transfer(
                table, [int(source.data_ptr)], block_ids, device,
                native.TransferDirection.D2H, desc, SLOTS, fmt, 0)
        stream.synchronize()
        report["format17"].update(stream_completion_waited=True,
                                  transfer_and_wait_seconds=time.monotonic() - started)
        report["npu_copy_performed"] = True
        source_view = adapter_view(_obj_to_memoryview, source, args.padded_view_reference,
                                   held_views, report)
        report["source_after"] = object_info(source, source_view, base, pool_bytes, disk_block)
        before, after = report["source_before"], report["source_after"]
        report["same_object_and_address"] = all(before[k] == after[k] for k in
                                                ("identity", "B_pool_base", "o_pool_offset",
                                                 "p_object", "q_view", "L_logical", "P_physical",
                                                 "view_bytes", "itemsize"))
        report["npu_logical_equal"] = bool(torch.equal(physical_tensors[0][:LOGICAL], expected_after[:LOGICAL]))
        report["padding_unchanged"] = bool(torch.equal(physical_tensors[0][LOGICAL:], expected_after[LOGICAL:]))
        reporter.save("NPU_D2H_COMPLETED")
        if not report["same_object_and_address"]:
            raise ProbeFailure("OBJECT_OR_ADDRESS_CHANGED", "same-object contract violated")
        if not report["npu_logical_equal"]:
            raise ProbeFailure("NPU_D2H_CONTENT_MISMATCH", "logical bytes differ from independent CPU reference")
        if not report["padding_unchanged"]:
            raise ProbeFailure("NPU_D2H_PADDING_OVERWRITE", "copy changed bytes outside logical payload")
        fs_phase("NPU_AFTER", expected_after)
        direct = all(p["direct_parameters_satisfied"] for p in report["fs_phases"].values())
        if not direct:
            report["status"] = "CHAIN_CONTENT_OK_BUFFERED_FALLBACK_NOT_DIO_PASS"
            return 3
        report["status"] = ("REFERENCE_PADDED_CHAIN_PASSED" if args.padded_view_reference
                            else "ACTUAL_ADAPTER_CHAIN_PASSED")
        return 0
    finally:
        cleanup_errors = []
        if stream is not None:
            cleanup_errors = confirm_stream_before_release(stream, reporter)
        else:
            report["cleanup_safe_to_release"] = True
        if client is not None:
            client.close()  # joins workers before any L1 pointer may be freed
        reporter.save("CLEANUP")
        owners.clear()
        if "physical_tensors" in locals():
            physical_tensors.clear()
        for view in held_views:
            view.release()
        held_views.clear()
        if allocator is not None:
            try:
                if held_objects:
                    allocator.batched_free(held_objects)
            finally:
                allocator.close()
        report["cleanup_errors"] = cleanup_errors
        reporter.save("CLEANUP_RETURNED")
        if cleanup_errors:
            raise ProbeFailure("NPU_CLEANUP_FAILED", "; ".join(cleanup_errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true", help="Print the static case without importing torch")
    parser.add_argument("--base-path", help="Existing private test parent directory on the SSD")
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--padded-view-reference", action="store_true",
                        help="Use test-local original-address physical view, not actual-adapter acceptance")
    args = parser.parse_args()
    if args.describe:
        print(json.dumps(description(), ensure_ascii=False, indent=2))
        return 0
    if not args.base_path:
        parser.error("--base-path is required unless --describe is used")
    if args.device_index < 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("device-index must be nonnegative and timeout finite/positive")
    base = Path(args.base_path).resolve(strict=True)
    if not base.is_dir():
        parser.error("base-path must be an existing directory")
    directory = Path(tempfile.mkdtemp(prefix="npu_dio_chain_", dir=str(base)))
    data = {"run_id": uuid.uuid4().hex, "run_directory": str(directory),
            "status": "NOT_COMPLETED", "case": description(), "fs_phases": {},
            "npu_copy_performed": False, "full_mp_adapter_tested": False,
            "customer_ds_layout_tested": False,
            "padded_view_reference": args.padded_view_reference,
            "report_path": str(directory / "result.json")}
    reporter = Reporter(directory / "result.json", data)
    reporter.save("STARTED")
    try:
        code = run(args, reporter)
    except BaseException as exc:
        data["status"] = exc.status if isinstance(exc, ProbeFailure) else "SETUP_OR_RUNTIME_ERROR"
        data["exception"] = f"{type(exc).__name__}: {exc}"
        data["traceback"] = traceback.format_exc()
        code = 130 if isinstance(exc, KeyboardInterrupt) else 2
    reporter.save("FINISHED")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
