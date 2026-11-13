"""Private, unintegrated single-file byte observation; no provenance attestation."""

import hashlib
import io
import os
import stat
from dataclasses import dataclass

_CHUNK = 65536


@dataclass(frozen=True)
class Metadata:
    device: int | None
    inode: int | None
    size: int | None
    mode: int | None
    mtime_ns: int | None
    ctime_ns: int | None
    regular: bool | None
    absent: tuple[str, ...]
    untrustworthy: tuple[str, ...]


@dataclass(frozen=True)
class Observation:
    status: str
    reason: str | None
    byte_count: int
    sha256: str | None
    second_byte_count: int
    second_sha256: str | None
    initial: Metadata | None
    final: Metadata | None
    metadata_differs: bool | None
    provenance: str = "opaque; caller assertion only"
    first_probe_count: int = 0
    second_probe_count: int = 0
    final_stat_error: str | None = None


def _metadata(fd: int) -> Metadata:
    s = os.fstat(fd)
    names = ("st_dev", "st_ino", "st_size", "st_mode", "st_mtime_ns", "st_ctime_ns")
    values = tuple(getattr(s, name, None) for name in names)
    absent = tuple(name for name, value in zip(names, values) if value is None)
    dev, ino, size, mode, mtime, ctime = values
    uncertain = ["path ancestry", "native file identity", "timestamp continuity", "ctime meaning", "provenance"]
    if dev in (None, 0):
        uncertain.append("st_dev")
    if ino in (None, 0):
        uncertain.append("st_ino")
    return Metadata(dev, ino, size, mode, mtime, ctime,
                    stat.S_ISREG(mode) if mode is not None else None,
                    absent, tuple(uncertain))


@dataclass(frozen=True)
class _Pass:
    # count refers only to sampled payload bytes, not the one-byte end probe.
    count: int
    sha256: str | None
    probe_count: int
    status: str | None
    reason: str | None


def _read_pass(fd: int, size: int) -> _Pass:
    digest = hashlib.sha256()
    count = 0
    while count < size:
        try:
            part = os.read(fd, min(_CHUNK, size - count))
        except (OSError, ValueError, OverflowError) as exc:
            return _Pass(count, None, 0, "UNPROVEN", "io_or_seek_error:" + type(exc).__name__)
        if not part:
            return _Pass(count, None, 0, "REFUSE", "short_read")
        count += len(part)
        digest.update(part)
    try:
        probe = os.read(fd, 1)
    except (OSError, ValueError, OverflowError) as exc:
        return _Pass(count, None, 0, "UNPROVEN", "io_or_seek_error:" + type(exc).__name__)
    if probe:
        return _Pass(count, None, len(probe), "REFUSE", "byte_count_mismatch")
    return _Pass(count, digest.hexdigest(), 0, None, None)


def observe_single_file(handle: io.FileIO, max_size: int) -> Observation:
    """Observe twice from an exact raw FileIO at offset zero; caller owns the handle."""
    initial = final = None
    count = count2 = 0
    probe_count = probe_count2 = 0
    digest = digest2 = None
    final_stat_error = None
    status, reason = "UNPROVEN", "not_sampled"
    fd = None
    if type(max_size) is not int or max_size <= 0:
        return Observation("REFUSE", "invalid_bound", 0, None, 0, None, None, None, None)
    if type(handle) is not io.FileIO or handle.closed or handle.mode != "rb":
        return Observation("REFUSE", "unsupported_handle", 0, None, 0, None, None, None, None)
    try:
        fd = handle.fileno()
        initial = _metadata(fd)
        if initial.regular is False:
            status, reason = "REFUSE", "nonregular"
        elif initial.regular is None or initial.absent or "st_dev" in initial.untrustworthy or "st_ino" in initial.untrustworthy:
            status, reason = "UNPROVEN", "missing_metadata_or_identity"
        elif initial.size < 0:
            status, reason = "UNPROVEN", "invalid_size"
        elif initial.size > max_size:
            status, reason = "REFUSE", "size_limit"
        elif os.lseek(fd, 0, os.SEEK_CUR) != 0:
            status, reason = "REFUSE", "position_not_zero"
        else:
            first = _read_pass(fd, initial.size)
            count, digest, probe_count = first.count, first.sha256, first.probe_count
            if first.status is not None:
                status, reason = first.status, first.reason
            else:
                os.lseek(fd, 0, os.SEEK_SET)
                second = _read_pass(fd, initial.size)
                count2, digest2, probe_count2 = second.count, second.sha256, second.probe_count
                if second.status is not None:
                    status, reason = second.status, second.reason
                elif digest != digest2:
                    status, reason = "REFUSE", "bytes_changed"
                else:
                    status, reason = "OBSERVED_UNATTESTED", None
    except (OSError, ValueError, OverflowError) as exc:
        status, reason = "UNPROVEN", "io_or_seek_error:" + type(exc).__name__
    finally:
        if fd is not None and initial is not None:
            try:
                final = _metadata(fd)
            except (OSError, ValueError, OverflowError) as exc:
                final_stat_error = "final_stat_error:" + type(exc).__name__
                if status == "OBSERVED_UNATTESTED" or reason == "not_sampled":
                    status, reason = "UNPROVEN", final_stat_error
    differs = None if initial is None or final is None else initial != final
    if differs:
        status, reason = "REFUSE", "metadata_mismatch" if reason is None else reason + ";metadata_mismatch"
    elif final is not None and (final.absent or "st_dev" in final.untrustworthy or "st_ino" in final.untrustworthy) and status == "OBSERVED_UNATTESTED":
        status, reason = "UNPROVEN", "missing_metadata_or_identity"
    return Observation(status, reason, count, digest, count2, digest2, initial, final, differs,
                       first_probe_count=probe_count, second_probe_count=probe_count2,
                       final_stat_error=final_stat_error)
