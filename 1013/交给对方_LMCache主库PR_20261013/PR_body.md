**What this PR does / why we need it**:

The FS L2 O_DIRECT path currently passes views of caller-managed memory directly to executor I/O. Accelerator host allocations can expose special mappings that fail file I/O with EFAULT. Keeping the allocation alive does not change its mapping type. In addition, cancelling the awaiting coroutine does not stop an already-running executor operation.

Use aligned ordinary host staging owned entirely by the I/O worker:

- Store snapshots the source before submitting work to the executor, copies the snapshot into aligned anonymous mmap memory, and writes from that memory.
- Load reads into worker-owned mmap memory and returns an independent payload only after a complete read. The coroutine copies it into the destination byte view before reporting a hit. A cancelled waiter cannot cause a late write into the original destination.
- A short write fails before the temporary file is published. Opening the temporary file with O_TRUNC prevents stale trailing data from a previous temporary file.

Buffered I/O retains its existing path. No configuration or public adapter interface changes are required.

**Special notes for your reviewers**:

This candidate was reconstructed from the reviewed rc7 reference patch and applied to `dev` at `110caf524c728b7b4489b2d4db40e2c72f79a879`. Comparison with the exact field-tested Ascend implementation and its raw logs is still pending; this draft does not claim that the candidate itself has passed Ascend acceptance.

Address alignment uses `lcm(PAGESIZE, statvfs.f_bsize)` and retains the existing deployment assumption that this multiple and the request sizes satisfy the target file's direct-I/O constraints. This is not a general alignment-discovery or buffered-fallback implementation. The reported field statx mask, `0x17ff`, does not contain `STATX_DIOALIGN` (`0x2000`), so it supplies no valid DIO alignment fields. Successful calls with O_DIRECT do not alone prove that the filesystem bypassed the page cache.

The staging adds copies and temporary memory; performance and peak-memory impact have not been measured. This patch addresses buffer ownership in the adapter, not the entire controller reservation/shutdown protocol or concurrent stores using a shared temporary filename. Related discussions: [#4388](https://github.com/LMCache/LMCache/pull/4388), [#5673](https://github.com/LMCache/LMCache/pull/5673), [#4839](https://github.com/LMCache/LMCache/pull/4839).

Validation on WSL Linux with Python 3.12.3 and CPU PyTorch 2.6.0:

- 56 tests passed across the new staging regression file and existing FS adapter delete/key tests, including the 9 MiB round trip with O_DIRECT requested. One RuntimeWarning was emitted by the unchanged delete-after-close path.
- Four targeted regression tests failed as expected against the unmodified base: borrowed/unaligned write memory, short-write publication, stale trailing temporary-file data, and partial destination mutation on a short read.
- Ruff, isort, mypy, codespell, and whitespace checks passed for the changed files.

The nine new tests exercise the public adapter API and cover aligned staging, ctypes byte-view copy-back, short I/O, stale temporary files, close during read/store, the buffered path, and the Linux round trip. Most fault-injection tests remove the flag at the syscall boundary to avoid filesystem-dependent outcomes; the explicit Linux round trip retains it. These results are local host validation, not Ascend acceptance or end-to-end serving validation.

**If applicable**:

- [ ] this PR contains user facing changes - docs added
- [x] this PR contains unit tests
