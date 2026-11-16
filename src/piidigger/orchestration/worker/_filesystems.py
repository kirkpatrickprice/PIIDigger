"""Is a directory on a network filesystem?  Used by local_files_only.

Answers are cached per st_dev, so each mounted filesystem is looked up once per
worker no matter how many directories it holds.  Any failure answers "local":
skipping a local directory by mistake loses data, scanning a remote one only
costs time.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys

# Linux fstypes that reach over the network.  FUSE types are matched by prefix.
_LINUX_REMOTE_FSTYPES: frozenset[str] = frozenset(
    {
        "9p",
        "afs",
        "ceph",
        "cifs",
        "davfs",
        "glusterfs",
        "lustre",
        "ncpfs",
        "nfs",
        "nfs4",
        "smb3",
        "smbfs",
        "sshfs",
    }
)
_LINUX_REMOTE_FUSE_PREFIXES: tuple[str, ...] = ("fuse.sshfs", "fuse.rclone", "fuse.s3fs", "fuse.gcsfuse")

# macOS <sys/mount.h>: set in statfs.f_flags for a filesystem on local storage.
_MNT_LOCAL: int = 0x00001000


class _DarwinStatfs(ctypes.Structure):
    """struct statfs from macOS <sys/mount.h> (64-bit inode layout)."""

    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


# st_dev -> answer.  One entry per mounted filesystem the worker has seen.
_remote_by_dev: dict[int, bool] = {}


def is_remote_filesystem(path: str, st_dev: int) -> bool:
    """True when path (whose stat reported st_dev) is on a network filesystem."""
    answer = _remote_by_dev.get(st_dev)
    if answer is None:
        answer = _remote_by_dev[st_dev] = _lookup_is_remote(path, st_dev)
    return answer


def _lookup_is_remote(path: str, st_dev: int) -> bool:
    try:
        if sys.platform == "darwin":
            return _darwin_is_remote(path)
        if sys.platform.startswith("linux"):
            fstype = _linux_fstype(st_dev)
            return fstype in _LINUX_REMOTE_FSTYPES or fstype.startswith(_LINUX_REMOTE_FUSE_PREFIXES)
    except Exception:
        return False
    return False


def _darwin_is_remote(path: str) -> bool:
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    # Intel macOS exports the 64-bit-inode layout under a suffixed name.
    try:
        statfs = libc["statfs$INODE64"]
    except AttributeError:
        statfs = libc.statfs
    buf = _DarwinStatfs()
    if statfs(os.fsencode(path), ctypes.byref(buf)) != 0:
        return False
    return not (buf.f_flags & _MNT_LOCAL)


def _linux_fstype(st_dev: int) -> str:
    """fstype of the mount whose device is st_dev, from /proc/self/mountinfo, or ''."""
    # Only ever called on Linux.  The check tells mypy so: os.major and
    # os.minor do not exist on Windows, and mypy checks the body on every platform.
    if sys.platform == "win32":
        return ""
    wanted = f"{os.major(st_dev)}:{os.minor(st_dev)}"
    with open("/proc/self/mountinfo", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            # "36 35 98:0 /mnt1 /mnt2 rw,noatime master:1 - ext3 /dev/root rw"
            fields = line.split()
            if len(fields) > 2 and fields[2] == wanted and "-" in fields:
                sep = fields.index("-")
                if sep + 1 < len(fields):
                    return fields[sep + 1]
    return ""
