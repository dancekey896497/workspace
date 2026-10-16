#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Exercise real L1 objects with the native FS client, without a model.

No cache is deleted and no package/source is patched. Run on the Linux test
environment that already provides LMCache and its native FS extension.
"""

import argparse
import ctypes
import inspect
import json
import os
from pathlib import Path
import select
import sys
import tempfile
import time
import traceback
import uuid


def address(view):
    return ctypes.addressof(ctypes.c_ubyte.from_buffer(view))


def callable_info(fn):
    try:
        source_file = inspect.getfile(fn)
    except (TypeError, OSError):
        source_file = None
    return {
        "type": type(fn).__qualname__,
        "module": getattr(fn, "__module__", None),
        "qualname": getattr(fn, "__qualname__", None),
        "file": source_file,
    }


def completion(client, future_id, timeout):
    deadline = time.monotonic() + timeout
    while True:
        for item in client.drain_completions():
            if int(item[0]) == future_id:
                return {
                    "future_id": int(item[0]),
                    "ok": bool(item[1]),
                    "error": str(item[2]),
                    "result_bools": item[3],
                }
            raise RuntimeError(f"unexpected completion id: {item[0]}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"native completion {future_id} timed out")
        select.select([client.event_fd()], [], [], min(remaining, 0.1))


def object_info(obj, view, pool_base, alignment, block_size):
    ptr = int(obj.data_ptr)
    view_ptr = address(view)
    offset = int(obj.meta.address)
    return {
        "object_type": type(obj).__name__,
        "pool_base": pool_base,
        "pool_base_remainder": None if pool_base is None else pool_base % block_size,
        "offset": offset,
        "offset_remainder": offset % block_size,
        "data_ptr": ptr,
        "data_ptr_hex": hex(ptr),
        "view_ptr": view_ptr,
        "view_ptr_hex": hex(view_ptr),
        "same_pointer": ptr == view_ptr,
        "ptr_equals_base_plus_offset": None if pool_base is None else ptr == pool_base + offset,
        "align_bytes": alignment,
        "logical_bytes": int(obj.get_size()),
        "physical_bytes": int(obj.get_physical_size()),
        "submitted_bytes": view.nbytes,
        "itemsize": view.itemsize,
        "ptr_remainder": ptr % block_size,
        "view_remainder": view_ptr % block_size,
        "length_remainder": view.nbytes % block_size,
        "direct_parameters_satisfied": (
            view_ptr % block_size == 0
            and view.nbytes % block_size == 0
            and hasattr(os, "O_DIRECT")
        ),
    }


def run(args, report):
    import torch

    if args.device == "npu":
        import torch_npu  # noqa: F401

        torch.npu.set_device(args.device_index)

    import lmcache
    import lmcache.lmcache_fs as fs_module
    from lmcache.v1.distributed.config import L1MemoryManagerConfig
    from lmcache.v1.distributed.l2_adapters.native_connector_l2_adapter import (
        _obj_to_memoryview,
    )
    from lmcache.v1.distributed.memory_manager.l1_memory_manager import (
        create_memory_allocator,
    )
    from lmcache.v1.memory_management import MemoryFormat

    backend = str(lmcache.torch_device_type)
    report.update({
        "torch_version": torch.__version__,
        "torch_device_type": backend,
        "lmcache_path": str(lmcache.__file__),
        "native_fs_path": str(fs_module.__file__),
        "npu_copy_performed": False,
        "actual_odirect_syscall": "NOT_VERIFIED_NO_TRACE",
        "device_ops_type": type(lmcache.device_ops).__qualname__,
        "alloc_pinned_ptr": callable_info(lmcache.device_ops.alloc_pinned_ptr),
    })
    if backend != args.device:
        raise RuntimeError(f"requested backend {args.device}, actual backend {backend}")
    if args.device == "npu":
        import lmcache_ascend

        report["ascend_path"] = str(lmcache_ascend.__file__)

    case_root = Path(report["run_directory"])
    block_size = int(os.statvfs(case_root).f_bsize)
    if block_size <= 0:
        raise RuntimeError("filesystem block size is unavailable")
    sizes = args.logical_bytes or [4 * block_size, 4 * block_size - 1]
    pool_bytes = args.pool_mib * 1024 * 1024
    required = 2 * args.count * sum(
        ((size + args.align_bytes - 1) // args.align_bytes) * args.align_bytes
        for size in sizes
    )
    if required > pool_bytes:
        raise ValueError(f"pool too small: need at least {required} bytes, configured {pool_bytes}")

    requested_lazy = args.allocator == "lazy"
    cfg = L1MemoryManagerConfig(
        size_in_bytes=pool_bytes,
        use_lazy=requested_lazy,
        init_size_in_bytes=pool_bytes,
        align_bytes=args.align_bytes,
        shm_name="",
        use_hugepages=False,
    )
    report.update({
        "filesystem_block_size": block_size,
        "requested_use_lazy": requested_lazy,
        "effective_use_lazy": cfg.use_lazy,
        "shm_name": cfg.shm_name,
        "pool_bytes": pool_bytes,
        "sizes": sizes,
        "count_per_size": args.count,
        "configured_use_odirect": True,
    })
    if requested_lazy and not cfg.use_lazy:
        report["status"] = "SKIP_LAZY_AUTO_DISABLED"
        return 3

    allocator = None
    client = None
    held_objects = []
    held_views = []
    try:
        allocator = create_memory_allocator(cfg)
        report["allocator_class"] = type(allocator).__qualname__
        report["allocator_class_file"] = inspect.getsourcefile(type(allocator))
        report["allocator_init_file"] = inspect.getsourcefile(type(allocator).__init__)
        pool = getattr(allocator, "buffer", None)
        if pool is None:
            pool = getattr(allocator, "_buffer", None)
        pool_base = None if pool is None else int(pool.data_ptr())
        report["pool_base_source"] = "allocator buffer" if pool_base is not None else "NOT_OBTAINED"
        report["pool_base"] = pool_base
        report["actual_pool_bytes"] = None if pool is None else int(pool.nbytes)

        client = fs_module.LMCacheFSClient(str(case_root), 2, "", True, 0)
        records = report["objects"]
        for logical in sizes:
            sources = allocator.batched_allocate(
                torch.Size([logical]), torch.uint8, args.count, fmt=MemoryFormat.KV_2LTD
            )
            if sources is None:
                raise RuntimeError(f"L1 source allocation failed for {logical} bytes")
            held_objects.extend(sources)
            destinations = allocator.batched_allocate(
                torch.Size([logical]), torch.uint8, args.count, fmt=MemoryFormat.KV_2LTD
            )
            if destinations is None:
                raise RuntimeError(f"L1 destination allocation failed for {logical} bytes")
            held_objects.extend(destinations)
            for index, (source, destination) in enumerate(zip(sources, destinations)):
                source.raw_tensor.fill_(1 + index % 255)
                destination.raw_tensor.zero_()
                source_view = _obj_to_memoryview(source, True)
                destination_view = _obj_to_memoryview(destination, True)
                held_views.extend([source_view, destination_view])
                if source_view.itemsize != 1 or destination_view.itemsize != 1:
                    raise RuntimeError("native FS must receive byte-oriented memoryviews")
                key = f"lmcache_dio_probe_{report['run_id']}@00000000@0@{uuid.uuid4().hex}"
                record = {
                    "key": key,
                    "index": index,
                    "source": object_info(source, source_view, pool_base, args.align_bytes, block_size),
                    "destination": object_info(destination, destination_view, pool_base, args.align_bytes, block_size),
                }
                records.append(record)
                fid = int(client.submit_batch_set([key], [source_view]))
                record["store"] = completion(client, fid, args.timeout)
                if not record["store"]["ok"]:
                    record["status"] = "STORE_FAILED"
                    continue
                fid = int(client.submit_batch_get([key], [destination_view]))
                record["load"] = completion(client, fid, args.timeout)
                load = record["load"]
                equal = (
                    load["ok"]
                    and load["result_bools"] == [True]
                    and bytes(source_view.cast("B")) == bytes(destination_view.cast("B"))
                )
                record["readback_equal"] = bool(equal)
                if not equal:
                    record["status"] = "READBACK_FAILED"
                elif not (record["source"]["direct_parameters_satisfied"] and record["destination"]["direct_parameters_satisfied"]):
                    record["status"] = "READBACK_OK_DIO_PRECONDITIONS_NOT_MET"
                else:
                    record["status"] = "READBACK_OK_DIRECT_PARAMETERS_MET"

        good = records and all(row["status"] == "READBACK_OK_DIRECT_PARAMETERS_MET" for row in records)
        report["status"] = "COMPONENT_CHECK_PASSED" if good else "COMPONENT_CHECK_FAILED_OR_FALLBACK"
        return 0 if good else 1
    finally:
        # The C++ client only holds raw pointers. Stop and join its workers
        # before freeing any L1 allocation, including after a completion timeout.
        if client is not None:
            client.close()
        for view in held_views:
            view.release()
        held_views.clear()
        if allocator is not None:
            try:
                if held_objects:
                    allocator.batched_free(held_objects)
            finally:
                allocator.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-path", required=True, help="Existing private test directory on the target SSD")
    parser.add_argument("--device", choices=("npu", "cpu"), default="npu")
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--allocator", choices=("lazy", "nonlazy-native"), default="lazy")
    parser.add_argument("--align-bytes", type=int, default=4096)
    parser.add_argument("--pool-mib", type=int, default=64)
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument("--logical-bytes", type=int, nargs="+")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.align_bytes <= 0 or args.align_bytes & (args.align_bytes - 1):
        parser.error("align-bytes must be a positive power of two")
    if args.count <= 0 or args.timeout <= 0 or args.pool_mib <= 0:
        parser.error("count, timeout and pool-mib must be positive")
    if args.logical_bytes and any(value <= 0 for value in args.logical_bytes):
        parser.error("logical byte counts must be positive")
    base = Path(args.base_path).resolve(strict=True)
    if not base.is_dir():
        parser.error("base-path must be an existing directory")
    run_dir = Path(tempfile.mkdtemp(prefix="fs_native_probe_", dir=str(base)))
    report = {
        "run_id": uuid.uuid4().hex,
        "run_directory": str(run_dir),
        "probe_layer": "REAL_L1_TO_NATIVE_FS_COMPONENT_ONLY",
        "full_mp_adapter_tested": False,
        "ds_model_tested": False,
        "npu_copy_performed": False,
        "objects": [],
    }
    try:
        code = run(args, report)
    except Exception as exc:
        report["status"] = "SETUP_OR_RUNTIME_ERROR"
        report["exception"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        code = 2
    report_path = run_dir / "result.json"
    report["report_path"] = str(report_path)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return code


if __name__ == "__main__":
    sys.exit(main())
