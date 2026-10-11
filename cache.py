"""Atomic, versioned simulation frames with a domain/slot ownership boundary."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import sys
import tempfile
import uuid

from .protocol import read_message, write_message

SCHEMA = 1
MARKER = "fumaris-liquid-cache.json"
_SAFE_SLOT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
LOCK = ".writer.lock"


def _read_text(path):
    try:
        return Path(path).read_text(encoding="ascii").strip() or None
    except (OSError, ValueError):
        return None


def _boot_id():
    return _read_text("/proc/sys/kernel/random/boot_id")


def _machine_id():
    return _read_text("/etc/machine-id")


def _namespace(kind):
    """Identity of this process's namespace, only where /proc describes this process."""
    try:
        if os.readlink("/proc/self") != str(os.getpid()):
            return None  # /proc belongs to another PID namespace.
        return os.readlink("/proc/self/ns/" + kind) or None
    except OSError:
        return None


def _pid_namespace():
    return _namespace("pid")


def _time_namespace():
    # Process start times are reported relative to the reader's time namespace.
    return _namespace("time")


# Recovery relies on exclusive create, rename and directory locks behaving as
# on a local disk, and on one machine id per machine. Anything else stays locked.
_LOCAL_FILESYSTEMS = frozenset({"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "zfs", "bcachefs", "jfs",
                                "reiserfs", "tmpfs", "ramfs", "overlay", "vfat", "exfat", "ntfs", "ntfs3",
                                "fuseblk", "ecryptfs"})


def _on_local_filesystem(path):
    try:
        target = Path(os.path.realpath(path)).parts
        best, kind = -1, None
        with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as mounts:
            for line in mounts:
                fields = line.split()
                separator = fields.index("-")
                point = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[4])).parts
                # The last matching line wins among equal mount points: it is mounted on top.
                if len(point) >= best and target[:len(point)] == point:
                    best, kind = len(point), fields[separator + 1]
        return kind in _LOCAL_FILESYSTEMS
    except (OSError, ValueError, IndexError):
        return False


def _process_start(pid):
    """Kernel start time of a live process, or None where it cannot be read."""
    try:
        data = Path(f"/proc/{pid}/stat").read_bytes()
        return data.rsplit(b")", 1)[1].split()[19].decode("ascii")
    except (OSError, ValueError, IndexError):
        return None


def _owner_record():
    pid = os.getpid()
    return json.dumps({"pid": pid, "host": socket.gethostname(), "machine": _machine_id(),
                       "ns": _pid_namespace(), "time": _time_namespace(), "boot": _boot_id(),
                       "start": _process_start(pid)}).encode("ascii")


def _owner_gone(data):
    """True only when the recorded writer provably no longer exists.

    Linux only. The record must name this machine, this PID and time namespace
    and a known boot; every doubt (another or unknown machine or sandbox, an
    unreadable or pre-0.2.2 record, a live process with the same PID and an
    unknown start time) keeps the slot locked.
    """
    if not sys.platform.startswith("linux"):
        return False
    try:
        record = json.loads(data.decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(record, dict):
        return False
    pid = record.get("pid")
    if type(pid) is not int or pid <= 0:
        return False
    here = {"host": socket.gethostname(), "machine": _machine_id(), "ns": _pid_namespace(), "boot": _boot_id()}
    for key in ("host", "machine", "ns"):
        if not isinstance(record.get(key), str) or not here[key] or record[key] != here[key]:
            return False
    # Absent on kernels without time namespaces; then it must be absent on both sides.
    if "time" not in record or record["time"] != _time_namespace():
        return False
    boot, start = record.get("boot"), record.get("start")
    if not isinstance(boot, str) or not boot or not here["boot"]:
        return False
    if boot != here["boot"]:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, OverflowError):
        return False
    current_start = _process_start(pid)
    return isinstance(start, str) and bool(start) and bool(current_start) and start != current_start


class Cache:
    def __init__(self, directory, domain_id, slot):
        try:
            self.domain_id = str(uuid.UUID(domain_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Liquid cache requires a valid domain UUID") from exc
        if not _SAFE_SLOT.fullmatch(slot) or slot in {".", ".."}:
            raise ValueError("Cache slot must contain 1–64 letters, numbers, dots, dashes or underscores")
        self.root = Path(directory).expanduser().resolve()
        self.slot = slot
        self.path = self.root / self.domain_id / slot
        self._lock_fd = None
        self._lock_release_started = False
        self._lock_release = None

    def _check_path(self):
        # Refuse links at the managed directory levels; never follow a moved
        # cache slot into another user's files during deletion or writes.
        for part in (self.root / self.domain_id, self.path):
            if part.is_symlink():
                raise RuntimeError("Liquid cache directories cannot be symbolic links")

    def _metadata(self):
        return {"schema": SCHEMA, "domain_id": self.domain_id, "slot": self.slot}

    def _verify(self):
        self._check_path()
        marker = self.path / MARKER
        if marker.is_symlink():
            raise RuntimeError("Liquid cache ownership marker cannot be a symbolic link")
        try:
            stored = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError("Directory is not an owned Aquaris cache") from exc
        if stored != self._metadata():
            raise RuntimeError("Liquid cache ownership or format does not match")

    def acquire(self):
        if self._lock_release_started:
            raise RuntimeError("Cache lock retirement is incomplete; retry release before acquiring")
        if self._lock_fd is not None:
            return self
        self._check_path()
        self.path.mkdir(parents=True, exist_ok=True)
        marker = self.path / MARKER
        if marker.exists():
            self._verify()
        else:
            # Do not adopt existing files: the marker grants deletion authority.
            if any(self.path.iterdir()):
                raise RuntimeError("Cache slot contains files without a liquid ownership marker")
            self._atomic_bytes(marker, json.dumps(self._metadata()).encode("utf-8"))
        self._lock_release = None
        self._lock_fd = self._create_lock("Cache slot is locked by another simulation; choose a different slot")
        self._write_owner(self._lock_fd)
        return self

    @staticmethod
    def _write_owner(fd):
        # Durable before any frame: an empty lock can never be recovered.
        os.write(fd, _owner_record())
        os.fsync(fd)

    def _create_lock(self, refusal):
        """Exclusively create the writer lock, first clearing a crashed writer's."""
        lock = self.path / LOCK
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            try:
                return os.open(lock, flags, 0o600)
            except FileExistsError as exc:
                if not self._remove_crashed_lock():
                    raise RuntimeError(refusal) from exc
            try:
                # Still exclusive: a normal acquirer may have won since the removal.
                return os.open(lock, flags, 0o600)
            except FileExistsError as exc:
                raise RuntimeError(refusal) from exc
        except FileNotFoundError as exc:
            raise RuntimeError("Cache slot was removed while it was being opened; try again") from exc

    def _remove_crashed_lock(self):
        """Remove the writer lock of a process that no longer exists.

        Returns True when the lock path is clear for a fresh exclusive create.
        Recoverers exclude each other with a kernel lock on the slot directory,
        which a crashed recoverer cannot leave behind. Any error keeps the slot
        locked.
        """
        try:
            import fcntl
        except ImportError:
            return False
        guard = fd = None
        try:
            if not _on_local_filesystem(self.path):
                return False
            guard = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Everything below names the lock inside the guarded directory
            # itself, not whatever the slot path points to later.
            try:
                fd = os.open(LOCK, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=guard)
            except FileNotFoundError:
                return True
            examined = os.fstat(fd)
            if not stat.S_ISREG(examined.st_mode) or not _owner_gone(os.read(fd, 4096)):
                return False
            # The examined descriptor stays open so its inode cannot be reused
            # by a new lock before the identity comparison and unlink.
            try:
                current = os.lstat(LOCK, dir_fd=guard)
            except FileNotFoundError:
                return True
            if (current.st_dev, current.st_ino) != (examined.st_dev, examined.st_ino):
                return False
            os.unlink(LOCK, dir_fd=guard)
            return True
        except (OSError, OverflowError, AttributeError):
            return False
        finally:
            for descriptor in (fd, guard):
                if descriptor is not None:
                    os.close(descriptor)

    @staticmethod
    def _atomic_bytes(path, data):
        fd, temp = tempfile.mkstemp(prefix=".liquid-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def release(self):
        if not self._lock_release_started and self._lock_fd is None:
            return
        self._lock_release_started = True
        if self._lock_release is None:
            self._lock_release = {"path": self.path / LOCK,
                                  "identity": None, "phase": "UNATTEMPTED",
                                  "close_error": None}
        receipt = self._lock_release
        if receipt["phase"] == "ATTEMPTED_UNCONFIRMED":
            raise RuntimeError("Cache lock close outcome is unverified; retirement remains blocked") from receipt["close_error"]
        if receipt["phase"] == "UNATTEMPTED":
            observed = os.fstat(self._lock_fd)
            if not stat.S_ISREG(observed.st_mode):
                raise RuntimeError("Owned cache lock descriptor is not a regular file")
            receipt["identity"] = (observed.st_dev, observed.st_ino)
            fd = self._lock_fd
            receipt["phase"] = "ATTEMPTED_UNCONFIRMED"
            self._lock_fd = None
            try:
                os.close(fd)
            except BaseException as exc:
                receipt["close_error"] = exc
                raise
            receipt["phase"] = "CONFIRMED"
        if receipt["phase"] == "CONFIRMED":
            self._check_path()
            try:
                observed = os.lstat(receipt["path"])
            except FileNotFoundError:
                pass
            else:
                if (not stat.S_ISREG(observed.st_mode) or
                        (observed.st_dev, observed.st_ino) != receipt["identity"]):
                    raise RuntimeError("Cache writer lock identity changed; retirement remains blocked")
                try:
                    os.unlink(receipt["path"])
                except FileNotFoundError:
                    pass
            receipt["phase"] = "RETIRED"
        self._lock_release_started = False

    @staticmethod
    def _frame_name(frame):
        if type(frame) is not int or not -100000 <= frame <= 100000:
            raise ValueError("Cache frame is outside the supported range")
        return f"frame_{frame:+07d}.flq"

    def write(self, frame, header, payload):
        if self._lock_release_started:
            raise RuntimeError("Cache lock retirement is incomplete; cannot write frames")
        if self._lock_fd is None:
            raise RuntimeError("Acquire the liquid cache before writing")
        self._verify()
        target = self.path / self._frame_name(frame)
        fd, temp = tempfile.mkstemp(prefix=".liquid-", dir=self.path)
        try:
            with os.fdopen(fd, "wb") as stream:
                saved_header = dict(header, frame=frame, cache_schema=SCHEMA)
                write_message(stream, saved_header, payload)
                os.fsync(stream.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def read(self, frame):
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self._verify()
        source = self.path / self._frame_name(frame)
        if source.is_symlink():
            raise RuntimeError("Liquid cache frames cannot be symbolic links")
        with source.open("rb") as stream:
            header, payload = read_message(stream)
            if stream.read(1) or header.get("frame") != frame or header.get("cache_schema") != SCHEMA:
                raise RuntimeError("Liquid cache frame is corrupt or incompatible")
            return header, payload

    def frames(self):
        if not self.path.exists():
            return []
        self._verify()
        frames = []
        for source in self.path.glob("frame_*.flq"):
            try:
                number = int(source.stem[6:])
                if source.name == self._frame_name(number) and not source.is_symlink():
                    frames.append(number)
            except ValueError:
                pass
        return sorted(frames)

    def _sweep_deleted(self):
        """Finish removals of this domain's slots that an earlier delete left behind."""
        domain = self.path.parent
        try:
            names = [entry.name for entry in domain.iterdir()]
        except OSError:
            return
        for name in names:
            doomed = domain / name
            if not re.fullmatch(r"\.deleting-[0-9a-f]{32}", name) or doomed.is_symlink() or not doomed.is_dir():
                continue
            try:
                stored = json.loads((doomed / MARKER).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # The marker grants deletion authority, as for a live slot.
            if isinstance(stored, dict) and stored.get("domain_id") == self.domain_id and stored.get("schema") == SCHEMA:
                shutil.rmtree(doomed, ignore_errors=True)
                if doomed.exists():
                    raise RuntimeError("Cache slot removal is incomplete; remove " + str(doomed))

    def delete(self):
        if self._lock_release_started:
            raise RuntimeError("Cache lock retirement is incomplete; cannot delete cache")
        if self._lock_fd is not None:
            raise RuntimeError("Stop the simulation before deleting its cache")
        self._check_path()
        self._sweep_deleted()
        if not self.path.exists():
            return
        self._verify()
        # Own the writer lock so no simulation can start in this slot, then
        # take the slot out of its path before removing anything. The lock is
        # its file, not the descriptor, which is closed so the rename also
        # works where open files pin their directory.
        lock = self.path / LOCK
        fd = self._create_lock("Cannot delete a cache slot while a simulation owns it")
        created = None
        try:
            try:
                created = os.fstat(fd)
                self._write_owner(fd)
            finally:
                os.close(fd)
            doomed = self.path.with_name(".deleting-" + uuid.uuid4().hex)
            os.rename(self.path, doomed)
        except BaseException:
            try:
                current = os.lstat(lock)
                if created is not None and (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                    os.unlink(lock)
            except OSError:
                pass
            raise
        shutil.rmtree(doomed, ignore_errors=True)
        if doomed.exists():
            raise RuntimeError("Cache slot removal is incomplete; remove " + str(doomed))

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()
