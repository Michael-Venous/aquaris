"""Atomic, versioned simulation frames with a domain/slot ownership boundary."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import uuid

from .protocol import read_message, write_message

SCHEMA = 1
MARKER = "fumaris-liquid-cache.json"
_SAFE_SLOT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


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
        lock = self.path / ".writer.lock"
        self._lock_release = None
        try:
            self._lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise RuntimeError("Cache slot is locked by another simulation; choose a different slot") from exc
        os.write(self._lock_fd, str(os.getpid()).encode("ascii"))
        return self

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
            self._lock_release = {"path": self.path / ".writer.lock",
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

    def delete(self):
        if self._lock_release_started:
            raise RuntimeError("Cache lock retirement is incomplete; cannot delete cache")
        if self._lock_fd is not None:
            raise RuntimeError("Stop the simulation before deleting its cache")
        if not self.path.exists():
            return
        self._verify()
        if (self.path / ".writer.lock").exists():
            raise RuntimeError("Cannot delete a cache slot while a simulation owns it")
        shutil.rmtree(self.path)

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()
