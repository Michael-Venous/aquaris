"""Process isolation for the native GPU runtime; no bpy access on worker threads."""
from collections import deque
import json
import mmap
import os
import platform
import secrets
import sys
from pathlib import Path
import subprocess
import threading
import tempfile

from .protocol import read_message, validate_blocks, write_message


class _WriteAttempt:
    """Track transport entry without changing the protocol serializer."""
    def __init__(self, stream):
        self.stream = stream
        self.attempted = False

    def write(self, data):
        self.attempted = True
        return self.stream.write(data)

    def flush(self):
        self.attempted = True
        return self.stream.flush()


def gpu_choice(mode, name, renderer, uuid=""):
    """Environment for the worker's GPU: `mode` AUTO, DEVICE, BLENDER or NAMED, `renderer` the GPU Blender draws on."""
    if mode == "DEVICE":
        if not uuid:
            raise RuntimeError("Choose a Simulation Device in Aquaris, or switch Simulation GPU to Auto")
        return {"FUMARIS_LIQUID_GPU": "uuid:" + uuid}
    if mode == "NAMED":
        return {"FUMARIS_LIQUID_GPU": name.strip()} if name and name.strip() else {}
    renderer = (renderer or "").split("/")[0].strip()  # OpenGL appends "/PCIe/SSE2"
    if not renderer:
        return {}
    return {"FUMARIS_LIQUID_GPU": renderer} if mode == "BLENDER" else {"FUMARIS_LIQUID_GPU_AVOID": renderer}


def gpu_environment():
    """The worker's GPU from the add-on preferences. Called on Blender's main thread (where a Bridge is made).
    Auto keeps the simulation off the GPU Blender draws on when there is another GPU of the same kind: sharing it
    with Blender's drawing halved a live preview's rate (2026-10-22, honey pour: 20 fps on one RTX 4060 Ti,
    44 fps with Blender drawing on the other card). Variables already in the environment win."""
    try:
        import bpy
        import gpu
    except ImportError:
        return {}
    addon = bpy.context.preferences.addons.get(__package__)
    preferences = addon.preferences if addon else None
    try:
        renderer = gpu.platform.renderer_get()
    except Exception:  # no GPU context (background Blender without one)
        renderer = ""
    # An explicit environment override wins even if the saved selection is missing/empty.
    if os.environ.get("FUMARIS_LIQUID_GPU"):
        return {}
    return gpu_choice(getattr(preferences, "simulation_gpu", "AUTO"),
                      getattr(preferences, "simulation_gpu_name", ""), renderer,
                      getattr(preferences, "simulation_gpu_uuid", ""))


def find_worker():
    suffix = ".exe" if os.name == "nt" else ""
    addon = Path(__file__).resolve().parent
    candidates = [addon / "bin" / ("fumaris_liquid_bridge" + suffix),
                  addon.parent / "build" / ("fumaris_liquid_bridge" + suffix),
                  addon.parent / "build" / "Release" / ("fumaris_liquid_bridge" + suffix)]
    for candidate in candidates:
        if candidate.is_file():
            # Blender's extension ZIP extractor does not restore Unix mode bits.
            # Restore only owner execution on our bundled, signed macOS worker.
            if sys.platform == "darwin" and candidate.parent == addon / "bin" and not os.access(candidate, os.X_OK):
                candidate.chmod(candidate.stat().st_mode | 0o100)
            return candidate
    raise RuntimeError("The Aquaris worker (the solver program) is missing from this installation. "
                       "Reinstall the Aquaris extension ZIP made for your operating system.")


def platform_tag():
    """The extension platform this Python runs on, as the package names it."""
    machine = platform.machine().lower()
    if sys.platform.startswith("linux") and machine in {"x86_64", "amd64"}:
        return "linux-x64"
    if os.name == "nt" and machine in {"x86_64", "amd64"}:
        return "windows-x64"
    if sys.platform == "darwin" and machine == "arm64":
        return "macos-arm64"
    return ""


def worker_problem(version=None):
    """Why the bundled worker cannot be used, in words for the user, or '' when it looks usable.

    Checks that the worker exists and, for a packaged install, that build_info.json (written by the packager)
    agrees with this computer's platform and with `version`, the add-on's version tuple."""
    try:
        worker = find_worker()
    except RuntimeError as exc:
        return str(exc)
    if os.name != "nt" and not os.access(worker, os.X_OK):
        return "The Aquaris worker is not executable. Reinstall the extension (a copy that lost its file permissions cannot run)."
    info = worker.parent.parent / "build_info.json"
    if not info.is_file():
        return ""  # a development checkout
    try:
        details = json.loads(info.read_text())
    except (OSError, ValueError):
        return ""
    tag = platform_tag()
    if tag and details.get("platform") and details["platform"] != tag:
        return f"This Aquaris package is for {details['platform']}, but Blender is running on {tag}. Install the matching ZIP."
    built = details.get("extension_version")
    # Blender's legacy bl_info is numeric; extension manifests may carry a
    # SemVer prerelease/build suffix (for example 0.3.35-beta.1).
    built_core = built.split("-", 1)[0].split("+", 1)[0] if isinstance(built, str) else built
    if version and built and built_core != ".".join(map(str, version)):
        return f"The worker is from Aquaris {built}, not {'.'.join(map(str, version))}. Restart Blender, or reinstall the extension."
    return ""


def _private_directory(path):
    """Create or accept a directory only this user can read; shared files are never trusted."""
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        stat = path.lstat()
        if path.is_symlink() or (hasattr(os, "getuid") and (stat.st_uid != os.getuid() or stat.st_mode & 0o077)):
            return None
        return path
    except OSError:
        return None


def _shader_cache_directory():
    """Compiled shader variants, kept across reboots.

    The temporary directory used before lost them at every restart, and each
    grid layout then cost seconds of compilation at the first Preview again.
    Entries are verified against their full source, so stale files are only
    unused; the oldest are removed because the worker stops saving at 512.
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Aquaris"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "Aquaris"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "aquaris"
    identity = str(os.getuid()) if hasattr(os, "getuid") else "user"
    for candidate in (base / "shaders", Path(tempfile.gettempdir()) / ("aquaris-shaders-" + identity)):
        cache = _private_directory(candidate)
        if cache is None:
            continue
        try:
            entries = sorted((entry for entry in cache.iterdir() if entry.suffix == ".spv-cache"),
                             key=lambda entry: entry.stat().st_mtime)
            for entry in entries[:max(0, len(entries) - 384)]:
                entry.unlink(missing_ok=True)
        except OSError:
            pass  # Pruning is best effort; caching stays optional.
        return cache
    return None


# First size of each of the two regions frame payloads are returned in. Only
# pages that are written take memory; a larger frame makes the regions grow.
RESPONSE_MEMORY = 256 * 1024 * 1024


class _ResponseMemory:
    """Two POSIX shared-memory regions the worker writes frame payloads into, in turn.

    The files are removed as soon as both processes have them mapped, so nothing
    is left behind if either dies. A payload handed out is a view of a region and
    stays valid for exactly one further frame; the mapping itself lives as long
    as any view of it does.
    """
    def __init__(self, size):
        self.size, self.names, self.maps = size, [], []
        try:
            for index in range(2):
                name = f"/aquaris-{os.getpid()}-{secrets.token_hex(6)}-{index}"
                descriptor = os.open("/dev/shm" + name, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                self.names.append(name)
                try:
                    os.ftruncate(descriptor, size)
                    self.maps.append(mmap.mmap(descriptor, size))
                finally:
                    os.close(descriptor)
        except BaseException:
            self.unlink()
            raise

    def unlink(self):
        for name in self.names:
            try:
                os.unlink("/dev/shm" + name)
            except OSError:
                pass
        self.names = []


class Bridge:
    def __init__(self, executable=None):
        # Set before start() by a client that reads each frame's payload before
        # asking for the frame after next (the live session does).
        self.shared_responses = False
        self._memory = None
        self.executable = Path(executable) if executable else None
        self.process = None
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()  # pipelined frames: send_frame on one thread, receive_frame on another
        self._read_lock = threading.Lock()
        self.share_wanted = None
        self._lifecycle = threading.Lock()
        self._closed = threading.Event()
        self._stderr = deque(maxlen=32)
        self._reader = None
        self._reader_started = False
        self._cleanup_errors = deque(maxlen=32)
        self._cleanup_unverified = False
        self.gpu_environment = gpu_environment()

    def launch(self):
        executable = (self.executable or find_worker()).resolve()
        environment = os.environ.copy()
        if not environment.get("FUMARIS_LIQUID_GPU"):
            for key, value in self.gpu_environment.items():
                environment.setdefault(key, value)
        if "FUMARIS_LIQUID_SHADER_CACHE" not in environment:
            cache = _shader_cache_directory()
            if cache is not None:
                environment["FUMARIS_LIQUID_SHADER_CACHE"] = str(cache)
        with self._lifecycle:
            if self._closed.is_set():
                raise RuntimeError("Liquid worker was cancelled before starting")
            if self.process is not None:
                raise RuntimeError("Liquid worker is already running")
            self.process = subprocess.Popen([str(executable)], cwd=str(executable.parent),
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, bufsize=0, env=environment)
            try:
                self._reader = threading.Thread(target=self._drain_stderr, args=(self.process,), daemon=True,
                                                name="FumarisLiquidLog")
                self._reader.start()
                self._reader_started = True
            except Exception:
                self._closed.set()
                # start() may fail before or after assigning the thread identity.
                self._reader_started = bool(self._reader and self._reader.ident is not None)
                self._cleanup_owned_locked(self.process)
                raise
    def start(self, settings):
        self.launch()
        try:
            header, _ = self.request("init", {"settings": settings})
            if self.shared_responses and header.get("shared_memory"):
                self._share(RESPONSE_MEMORY)
            return header
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup:
                raise exc from cleanup
            raise

    def _drain_stderr(self, process):
        try:
            for line in iter(process.stderr.readline, b""):
                self._stderr.append(line.decode("utf-8", "replace").rstrip())
        except (OSError, ValueError):
            pass  # Cancellation can close the pipe while the reader is blocked.

    def _share(self, size):
        """Ask the worker to return frame payloads through shared memory; False leaves the pipe in use."""
        self._memory = None
        if (not sys.platform.startswith("linux") or not os.path.isdir("/dev/shm")
                or os.environ.get("FUMARIS_LIQUID_SHARED_MEMORY") == "0"):
            return False
        try:
            memory = _ResponseMemory(size)
        except (OSError, ValueError):
            return False
        try:
            self._exchange("transport", {"response_memory": memory.names, "size": size})
        except RuntimeError:
            return False
        finally:
            memory.unlink()
        self._memory = memory
        return True

    def request(self, action, header=None, payload=b""):
        response, data = self._exchange(action, header, payload)
        needed = response.pop("payload_overflow", None)
        if needed is not None and self._memory is not None:
            # This frame came through the pipe; make room for the following ones.
            self._share(max(2 * int(needed), 2 * self._memory.size))
        return response, data

    def _exchange(self, action, header=None, payload=b""):
        with self._lock:
            process = self._send_locked(action, header, payload)
            return self._receive_locked(process)

    # Pipelined frames: send_frame() writes a frame request without waiting for its response, and
    # receive_frame() returns the responses in the order the requests were sent. The worker makes a frame's
    # response while it simulates the next one (src/worker.cpp, "pipelined"). The caller keeps at most two
    # frames in flight, reads a frame's shared-memory payload before it sends the request after the next (the
    # two regions alternate), and sends nothing else while any is in flight.
    def send_frame(self, header, payload=b""):
        with self._write_lock:
            message = dict(header or {})
            message["pipelined"] = True
            self._send_locked("frame", message, payload)

    def receive_frame(self):
        with self._read_lock:
            process = self.process
            if process is None:
                raise RuntimeError(self._failure("Liquid worker is not running"))
            response, data = self._receive_locked(process)
        needed = response.pop("payload_overflow", None)
        if needed is not None and self._memory is not None:
            self.share_wanted = max(2 * int(needed), 2 * self._memory.size)  # made room for once nothing is in flight
        return response, data

    def reshare(self):
        """Grow the shared response memory after a frame overflowed it (nothing may be in flight)."""
        wanted, self.share_wanted = getattr(self, "share_wanted", None), None
        if wanted is not None and self._memory is not None:
            self._share(wanted)

    def _send_locked(self, action, header, payload):
        process = self.process
        if self._closed.is_set() or process is None:
            raise RuntimeError(self._failure("Liquid worker is not running"))
        try:
            exited = process.poll() is not None
        except OSError as exc:
            self._closed.set()
            self._join_log_reader(.2)
            raise RuntimeError(self._failure(str(exc))) from exc
        if exited:
            self._closed.set()
            self._join_log_reader(.2)
            raise RuntimeError(self._failure("Liquid worker is not running"))
        message = dict(header or {})
        message["action"] = action
        writer = _WriteAttempt(process.stdin)
        try:
            write_message(writer, message, payload)
        except Exception as exc:
            if writer.attempted:
                self._closed.set()
                self._join_log_reader(.2)
            # Keep the existing public error conversion, but classify by
            # attempted output rather than exception type. A TypeError can
            # also occur after the protocol envelope/header was written.
            if isinstance(exc, (EOFError, OSError, ValueError)):
                raise RuntimeError(self._failure(str(exc))) from exc
            raise
        # Tell a waiting caller the worker has its request and is working on it.
        callback, self.on_sent = getattr(self, "on_sent", None), None
        if callback is not None:
            callback()
        return process

    def _receive_locked(self, process):
        try:
            response, data = read_message(process.stdout)
        except Exception as exc:
            self._closed.set()
            self._join_log_reader(.2)
            if isinstance(exc, (EOFError, OSError, ValueError)):
                raise RuntimeError(self._failure(str(exc))) from exc
            raise
        if not response.get("ok", False):
            if response.get("fatal") is True:
                self._closed.set()
            raise RuntimeError(response.get("error", "Liquid worker failed"))
        index = response.pop("payload_memory", None)
        if index is not None:
            size = int(response.pop("payload_bytes"))
            memory = self._memory
            if memory is None or not 0 <= int(index) < 2 or not 0 <= size <= memory.size:
                self._closed.set()
                raise RuntimeError(self._failure("Liquid worker returned a payload outside the shared memory"))
            data = memoryview(memory.maps[int(index)])[:size]
            validate_blocks(response, data)
        return response, data

    def _failure(self, message):
        tail = "\n".join(tuple(self._stderr))
        return message + ("\n" + tail if tail else "")

    def _record_cleanup_error(self, stage, error):
        self._cleanup_errors.append(stage + ": " + str(error))

    def _join_log_reader(self, timeout):
        reader = self._reader
        if self._reader_started and reader is not None and reader is not threading.current_thread():
            try:
                reader.join(timeout=timeout)
            except Exception as exc:
                self._record_cleanup_error("log reader join", exc)

    def _cleanup_owned_locked(self, process):
        """Lifecycle lock is held; retain ownership unless cleanup is verified."""
        exited = False
        try:
            exited = process.poll() is not None
        except Exception as exc:
            self._record_cleanup_error("poll", exc)
        if not exited:
            terminated = False
            try:
                process.terminate()
                terminated = True
            except Exception as exc:
                self._record_cleanup_error("terminate", exc)
            if terminated:
                try:
                    exited = process.wait(timeout=3) is not None
                except Exception as exc:
                    self._record_cleanup_error("terminate wait", exc)
            if not exited:
                try:
                    process.kill()
                except Exception as exc:
                    self._record_cleanup_error("kill", exc)
                # Even a failed signal can race with normal child exit.
                try:
                    exited = process.wait(timeout=3) is not None
                except Exception as exc:
                    self._record_cleanup_error("kill wait", exc)
        self._join_log_reader(1)
        streams_closed = True
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, name, None)
            if stream is not None:
                try:
                    stream.close()
                except Exception as exc:
                    streams_closed = False
                    self._record_cleanup_error(name + " close", exc)
        reader_stopped = True
        if self._reader_started and self._reader is not None:
            try:
                reader_stopped = not self._reader.is_alive()
            except Exception as exc:
                reader_stopped = False
                self._record_cleanup_error("log reader status", exc)
        self._cleanup_unverified = not (exited and streams_closed and reader_stopped)
        if not exited:
            self._record_cleanup_error("owned process exit", "not verified; ownership retained")
        if not reader_stopped:
            self._record_cleanup_error("log reader exit", "not verified; ownership retained")
        if not self._cleanup_unverified:
            self.process = None
            self._reader = None
            self._reader_started = False
        return not self._cleanup_unverified

    def close(self):
        # Never queue a close behind a long GPU request. Closing is also used by
        # cancellation and must terminate a stuck child within a bounded time.
        self._closed.set()
        with self._lifecycle:
            process = self.process
            if process is None:
                return
            if not self._cleanup_owned_locked(process):
                details = "\n".join(tuple(self._cleanup_errors))
                raise RuntimeError(self._failure(
                    "Liquid worker cleanup incomplete; owned process retained for retry" +
                    ("\n" + details if details else "")))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
