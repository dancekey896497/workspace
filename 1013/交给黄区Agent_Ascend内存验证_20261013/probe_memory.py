#!/usr/bin/env python3
"""Controlled host-memory/file probe; optional NPU transfer API round trip.

Runs in a fresh process. It never edits an installed package, calls a cache
adapter, or deletes an existing file. Native mode requires Ascend hardware.
"""
from __future__ import annotations

import argparse
import ctypes
import importlib
import json
import math
import mmap
import os
from pathlib import Path
import platform
import struct
import sys


def error_info(exc):
    return {"type": type(exc).__name__, "errno": getattr(exc, "errno", None),
            "message": str(exc)}


def dio_constraints(path):
    # Linux struct statx: 256-byte ABI; DIO fields at offsets 152 and 156.
    out = {"status": "UNVERIFIED", "path": str(path), "request_mask": "0x2000"}
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fn = libc.statx
        fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_uint, ctypes.c_void_p]
        fn.restype = ctypes.c_int
        buf = ctypes.create_string_buffer(256)
        rc = fn(-100, os.fsencode(path), 0, 0x2000, ctypes.byref(buf))
        if rc != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        mask = struct.unpack_from("=I", buf.raw, 0)[0]
        mem, offset = struct.unpack_from("=II", buf.raw, 152)
        out.update(return_mask=hex(mask), valid=bool(mask & 0x2000))
        if mask & 0x2000:
            out.update(mem_align=mem, offset_align=offset,
                       status="VALID" if mem and offset else "DIO_UNSUPPORTED")
    except Exception as exc:
        out["error"] = error_info(exc)
    return out


def vma_info(address):
    # Read only the mapping containing this pointer, not the entire process dump.
    out = {"address": hex(address), "status": "UNVERIFIED"}
    try:
        active = False
        with open("/proc/self/smaps", encoding="utf-8") as source:
            for line in source:
                first = line.split()[0] if line.split() else ""
                if "-" in first:
                    try:
                        lo, hi = (int(x, 16) for x in first.split("-"))
                    except ValueError:
                        continue
                    if active:
                        break
                    active = lo <= address < hi
                    if active:
                        out.update(mapping=line.strip(), status="FOUND")
                elif active and line.startswith("VmFlags:"):
                    out["vm_flags"] = line.split(":", 1)[1].strip()
                    break
    except Exception as exc:
        out["error"] = error_info(exc)
    return out


class Buffer:
    def __init__(self, mode, size, alignment, native, allocator):
        self.mode, self.size, self.native = mode, size, native
        self.owner, self.array, self.view = None, None, None
        self.address = 0
        if mode == "mmap":
            self.owner = mmap.mmap(
                -1, size + alignment - 1,
                flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS,
                prot=mmap.PROT_READ | mmap.PROT_WRITE)
            base = ctypes.addressof(ctypes.c_char.from_buffer(self.owner))
            start = (-base) % alignment
            self.view = memoryview(self.owner)[start:start + size]
            self.address = base + start
        else:
            if allocator == "plain":
                self.address = int(native.alloc_pinned_ptr(size, 0))
            else:
                self.address = int(native.alloc_pinned_numa_ptr(size, 0))
            if not self.address:
                raise RuntimeError("native allocator returned a null pointer")
            self.array = (ctypes.c_ubyte * size).from_address(self.address)
            self.view = memoryview(self.array).cast("B")

    def close(self, allocator):
        self.view.release()
        self.view, self.array = None, None
        if self.mode == "mmap":
            self.owner.close()
        elif allocator == "plain":
            self.native.free_pinned_ptr(self.address)
        else:
            self.native.free_pinned_numa_ptr(self.address, self.size)


def device_pointer(native, address):
    if native is None or not hasattr(native, "get_device_ptr"):
        return {"status": "UNVERIFIED", "reason": "no exported query"}
    try:
        value = native.get_device_ptr(address)
        return {"status": "QUERY", "value": hex(int(value or 0))}
    except Exception as exc:
        return {"status": "QUERY_ERROR", "error": error_info(exc)}


def io_case(src, dst, expected, path, op, direct, constraints):
    out = {"op": op, "direct_requested": direct, "requested": len(expected),
           "file_offset": 0, "constraints": constraints}
    buf = src if op == "write" else dst
    out["syscall_buffer_address"] = hex(buf.address)
    if constraints.get("status") == "VALID":
        mem, offset = constraints["mem_align"], constraints["offset_align"]
        out["address_mod_mem"] = buf.address % mem
        out["length_mod_offset"] = len(expected) % offset
        out["offset_mod_offset"] = 0
        if direct and (buf.address % mem or len(expected) % offset):
            out["status"] = "PARAMETERS_UNALIGNED"
            return out
    if direct and (not hasattr(os, "O_DIRECT") or
                   constraints.get("status") == "DIO_UNSUPPORTED"):
        out["status"] = "DIO_UNSUPPORTED"
        return out
    fd = None
    try:
        flags = os.O_WRONLY | os.O_TRUNC if op == "write" else os.O_RDONLY
        if direct:
            flags |= os.O_DIRECT
        out["open_flags"] = flags
        fd = os.open(path, flags)
        if op == "write":
            count = os.write(fd, src.view)  # No bytes(), no staging at this boundary.
            os.close(fd)
            fd = None
            observed = path.read_bytes()
        else:
            dst.view[:] = b"?" * len(expected)
            count = os.readv(fd, [dst.view])
            observed = bytes(dst.view)
        out.update(returned=count, file_size=path.stat().st_size,
                   content_equal=(observed == expected))
        out["status"] = "PASS" if count == len(expected) and observed == expected else "INCOMPLETE_OR_MISMATCH"
    except Exception as exc:
        out.update(status="ERROR", error=error_info(exc))
    finally:
        if fd is not None:
            os.close(fd)
    return out


def npu_round_trip(torch, src, dst, expected, device, path, constraints):
    # frombuffer keeps the CPU tensor on exactly this buffer; it does not copy.
    host_src = torch.frombuffer(src.view, dtype=torch.uint8)
    host_dst = torch.frombuffer(dst.view, dtype=torch.uint8)
    out = {"status": "UNVERIFIED", "meaning": "TRANSFER_API_ONLY",
           "source_tensor_address": hex(host_src.data_ptr()),
           "destination_tensor_address": hex(host_dst.data_ptr())}
    if host_src.data_ptr() != src.address or host_dst.data_ptr() != dst.address:
        raise RuntimeError("torch wrapper is not using the tested host pointers")
    dev = host_src.to(device, non_blocking=True)
    torch.npu.synchronize()
    host_src.fill_(0)
    host_src.copy_(dev, non_blocking=True)
    torch.npu.synchronize()
    out["device_to_host_equal"] = bytes(src.view) == expected
    written = io_case(src, dst, expected, path, "write", True, constraints)
    loaded = io_case(src, dst, expected, path, "read", True, constraints)
    out["file_io"] = [written, loaded]
    if not out["device_to_host_equal"] or any(x["status"] != "PASS" for x in out["file_io"]):
        out["status"] = "DEVICE_TO_HOST_OR_FILE_FAILED"
        return out
    dev.zero_()
    dev.copy_(host_dst, non_blocking=True)
    torch.npu.synchronize()
    host_src.fill_(0)
    host_src.copy_(dev, non_blocking=True)
    torch.npu.synchronize()
    equal = bytes(src.view) == expected
    out.update(content_equal=equal, status="PASS" if equal else "MISMATCH")
    # The runtime may internally stage pageable host memory. This is not proof
    # of direct mapped-kernel access, pinning, or absence of hidden copies.
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", required=True, help="new directory; must not exist")
    parser.add_argument("--mode", choices=["mmap", "native"], required=True)
    parser.add_argument("--module", default="lmcache_ascend.c_ops")
    parser.add_argument("--allocator", choices=["plain", "numa0"], default="plain")
    parser.add_argument("--size", type=int, default=9 * 1024 * 1024)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--npu", action="store_true", help="also test torch NPU transfer API")
    parser.add_argument("--device", default="npu:0")
    args = parser.parse_args()
    if sys.platform != "linux" or args.size <= 0 or not 1 <= args.iterations <= 5:
        parser.error("requires Linux, positive size, and 1..5 iterations")
    root = Path(args.work_dir).resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {"mode": args.mode, "allocator": args.allocator, "size": args.size,
              "python": sys.executable, "kernel": platform.release(),
              "work_dir": str(root), "npu_requested": args.npu,
              "device": args.device, "rounds": [], "overall": "UNVERIFIED"}
    native, torch = None, None
    npu_quiescent = True
    try:
        if args.mode == "native" or args.npu:
            torch = importlib.import_module("torch")
            importlib.import_module("torch_npu")
            if not torch.npu.is_available():
                raise RuntimeError("Ascend NPU unavailable; do not replace with CPU")
            torch.npu.set_device(args.device)
            report["torch_version"] = str(torch.__version__)
        if args.mode == "native":
            native = importlib.import_module(args.module)
            report["native_module"] = {"name": args.module,
                "path": str(Path(native.__file__).resolve()),
                "alloc_module": getattr(native.alloc_pinned_ptr, "__module__", None)}
        expected = (bytes(range(256)) * ((args.size + 255) // 256))[:args.size]
        for index in range(args.iterations):
            case_dir = root / ("round_%02d" % index)
            case_dir.mkdir()
            write_path, read_path = case_dir / "write.data", case_dir / "read.data"
            with write_path.open("xb"):
                pass
            with read_path.open("xb") as stream:
                if stream.write(expected) != len(expected):
                    raise RuntimeError("failed to prepare the independent read control")
            wc, rc = dio_constraints(write_path), dio_constraints(read_path)
            known = [c["mem_align"] for c in (wc, rc) if c.get("status") == "VALID"]
            alignment = math.lcm(mmap.PAGESIZE, *known)
            round_result = {"index": index, "staging_alignment": alignment,
                            "io": [], "cleanup": []}
            report["rounds"].append(round_result)
            owned = []
            try:
                for label in ("source", "destination"):
                    buf = Buffer(args.mode, args.size, alignment, native, args.allocator)
                    owned.append((label, buf))
                    round_result[label] = {"address": hex(buf.address),
                        "address_mod_page": buf.address % mmap.PAGESIZE,
                        "vma": vma_info(buf.address),
                        "device_pointer": device_pointer(native, buf.address)}
                src, dst = owned[0][1], owned[1][1]
                src.view[:] = expected
                for direct in (False, True):
                    round_result["io"].append(io_case(src, dst, expected, write_path, "write", direct, wc))
                    round_result["io"].append(io_case(src, dst, expected, read_path, "read", direct, rc))
                if args.npu:
                    npu_quiescent = False
                    round_result["npu"] = npu_round_trip(
                        torch, src, dst, expected, args.device, write_path, wc)
                    npu_quiescent = True
            finally:
                if torch is not None:
                    try:
                        torch.npu.synchronize()
                        npu_quiescent = True
                    except Exception as exc:
                        npu_quiescent = False
                        round_result["sync_error"] = error_info(exc)
                for label, buf in reversed(owned):
                    if not npu_quiescent:
                        round_result["cleanup"].append({"label": label, "status": "SKIPPED_UNCONFIRMED_DEVICE_COMPLETION"})
                        continue
                    try:
                        buf.close(args.allocator)
                        round_result["cleanup"].append({"label": label, "status": "FREE_RETURNED",
                            "device_pointer_after_free": device_pointer(native, buf.address)})
                    except Exception as exc:
                        round_result["cleanup"].append({"label": label, "status": "FREE_ERROR", "error": error_info(exc)})
                if not npu_quiescent:
                    raise RuntimeError("device completion unconfirmed; stop this process")
            if any(item["status"] != "PASS" for item in round_result["io"]):
                break
            if any(item["status"] != "FREE_RETURNED" for item in round_result["cleanup"]):
                break
        good = len(report["rounds"]) == args.iterations
        for item in report["rounds"]:
            good = good and len(item["io"]) == 4 and all(x["status"] == "PASS" for x in item["io"])
            good = good and len(item["cleanup"]) == 2 and all(x["status"] == "FREE_RETURNED" for x in item["cleanup"])
            if args.npu:
                good = good and item.get("npu", {}).get("status") == "PASS"
        report["overall"] = ("PASS_TRANSFER_API_ONLY" if args.npu else "PASS_HOST_IO_ONLY") if good else "NOT_PASSED"
    except Exception as exc:
        report.update(overall="NOT_PASSED", fatal=error_info(exc))
    finally:
        text = json.dumps(report, ensure_ascii=False, indent=2)
        (root / "result.json").write_text(text, encoding="utf-8")
        print("BEGIN_LMCACHE_MEMORY_REPORT\n" + text + "\nEND_LMCACHE_MEMORY_REPORT")
    return 0 if report["overall"].startswith("PASS_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
