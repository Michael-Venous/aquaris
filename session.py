"""One serialized native worker; all Blender access stays on the main thread."""

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import os
import threading
import json
import time

import bpy
from bpy.app.handlers import persistent

from . import display, exporters
from .bridge import Bridge
from .cache import Cache
from ._tasks import result_owned, submit_owned

_active = None
_playback_guard = False
_render_frames = {}  # Scene pointer -> time being rendered, in frames.
_shown_frames = {}  # Scene pointer -> cached frame last shown by playback.


def active():
    return _active


def cache_for(domain):
    p = domain.fumaris_liquid
    identity = display.ensure_identity(domain)
    directory = bpy.path.abspath(p.cache_directory)
    return Cache(directory, identity, p.cache_slot)


class Session:
    def __init__(self, scene, domain, bake=False):
        self.scene = scene
        self.domain = domain
        self.bake = bake
        self.recording = bake or domain.fumaris_liquid.record_preview
        self.cache = cache_for(domain) if self.recording else None
        self.exporter = exporters.SceneExporter(scene, domain)
        self.bridge = Bridge()
        # The loop reads a frame before it asks for the one after next, which is what shared payloads need.
        self.bridge.shared_responses = True
        self.future = None
        self._tickets = []
        self._work_tickets = []
        self._cache_release_error = None
        self.stopper = None
        self.phase = "STARTING"
        self._prepared = None
        self.paused = False
        self._remesh = False
        self.stopping = False
        self.error = None
        self._cleanup_retry_at = 0
        self._last_cleanup_report = None
        self.frame = domain.fumaris_liquid.bake_start if bake else scene.frame_current
        self.last_frame = domain.fumaris_liquid.bake_end if bake else None
        if bake and self.last_frame < self.frame:
            raise RuntimeError("Bake end must be at least the start frame")
        self.last_settings = self._settings()
        self.last_response = None
        # Pipelined frames (Bridge.send_frame): the next frame's request is with the worker while it meshes and
        # sends this one, so its surface and its next simulation overlap. FUMARIS_LIQUID_PIPELINE=0: one at a time.
        self.pipelined = os.environ.get("FUMARIS_LIQUID_PIPELINE", "1") != "0"
        self.inflight = deque()  # (frame, preview only, sent at, export ms) of the requests sent, oldest first
        self._show_at = 0.0  # when the next held live frame may be shown (Preview Rate)
        if not domain.fumaris_liquid.emitter_collection:
            raise RuntimeError("Assign an emitter collection or use Quick Setup")
        # No acquired cache, task or child exists before module start publishes
        # this complete owner and its cleanup timer.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="FumarisLiquid")

    def begin(self):
        if self.cache:
            self.cache.acquire()
            # Check while holding the slot lock, through the retained owner.
            if self.cache.frames():
                raise RuntimeError("This cache slot already contains frames; choose an empty slot or delete its cache")
        self.domain.fumaris_liquid.last_error = ""
        self.domain.fumaris_liquid.runtime_warning = ""
        self.domain.fumaris_liquid.status = "Starting Vulkan liquid solver…"
        self._submit(self.bridge.start, self.last_settings, work=True)

    def _submit(self, function, *args, work=False):
        # Retire terminal receipts while preserving every running/pending task.
        self._tickets = [ticket for ticket in self._tickets if not ticket.done()]
        self._work_tickets = [ticket for ticket in self._work_tickets if not ticket.done()]
        ticket = Future()
        self.future = ticket
        self._tickets.append(ticket)
        if work:
            self._work_tickets.append(ticket)
        submit_owned(self.executor, ticket, function, *args)

    def _cancel_pending(self):
        for ticket in self._tickets:
            ticket.cancel()

    def _verify_work_complete(self):
        if any(not ticket.done() for ticket in self._work_tickets):
            raise RuntimeError("Liquid task completion not verified; Session and cache ownership retained")

    def _verify_tasks_complete(self):
        if any(not ticket.done() for ticket in self._tickets):
            raise RuntimeError("Liquid task completion not verified; Session and cache ownership retained")

    def _release_cache(self):
        # The paired Cache retains its own current-lifetime retirement receipt.
        # A normal return confirms retirement; exceptions preserve retry state.
        if self.cache:
            try:
                self.cache.release()
            except BaseException as exc:
                if self._cache_release_error is None:
                    self._cache_release_error = exc
                if not isinstance(exc, Exception):
                    if self.error is None:
                        self.error = str(exc) or type(exc).__name__
                    raise  # Direct interrupts keep their normal semantics.
                raise RuntimeError("Cache cleanup failed; Session ownership retained") from exc

    def _request_frame(self):
        header, payload, update = self._prepared or self._export_frame()
        self._prepared = None
        self.phase = "FRAME"
        self._sent = threading.Event()
        self._submit(self._step, header, payload, update, self._sent, work=True)

    def _preview_interval(self):
        """Seconds between live frames shown, or 0 (Preview Rate: no cap; a bake is never held)."""
        if self.bake:
            return 0.0
        rate = getattr(self.domain.fumaris_liquid, "preview_rate", "UNLIMITED")
        if rate == "FPS_24":
            return 1 / 24
        if rate == "SCENE":
            render = self.scene.render
            return render.fps_base / max(render.fps, 1)
        return 0.0

    def _holding(self):
        """Whether a received live frame waits for its time (Preview Rate). The worker meanwhile finishes what it
        has been sent and then rests."""
        if self.phase != "FRAME" or self.stopping or not self.future.done():
            return False
        interval = self._preview_interval()
        if interval <= 0:
            self._show_at = 0.0
            return False
        now = time.monotonic()
        if now < self._show_at:
            return True
        # the next frame's time; after a stall the cadence starts again from now instead of catching up
        due = self._show_at if now - self._show_at < interval else now
        self._show_at = due + interval
        return False

    def wait_seconds(self):
        """How long the session has nothing to do: a held frame's remaining wait (for the timer's interval)."""
        if self.phase != "FRAME" or self.future is None or not self.future.done() or self.stopping:
            return 0.0
        return max(0.0, self._show_at - time.monotonic()) if self._preview_interval() > 0 else 0.0

    def _send_next(self):
        """Pipelined: export the next frame and send it without waiting for the worker (main thread)."""
        header, payload, update = self._prepared or self._export_frame()
        self._prepared = None
        preview_only = header.pop("_preview_only", False)
        if update is not None:
            header["settings_update"] = update
        self.bridge.send_frame(header, payload)
        self.inflight.append((header["frame"], preview_only, time.perf_counter(), self.export_ms))
        self.frame += 1

    def _receive_next(self):
        """Pipelined: the oldest request's response, cached (worker thread)."""
        frame, preview_only, sent, export_ms = self.inflight[0]
        response, result = self.bridge.receive_frame()
        response["worker_ms"] = (time.perf_counter() - sent) * 1000
        response["export_ms"] = export_ms
        response["frame"] = frame
        if preview_only:
            response["preview_only"] = True
        if self.cache is not None:
            self.cache.write(frame, response, result)
        return response, result

    def _poll_pipelined(self):
        shown = None
        if self.future is not None and not self.future.done():
            if self.phase == "STARTING" and self._prepared is None and not self.stopping:
                try:
                    self._prepared = self._export_frame()
                except Exception as exc:
                    if self.error is None:
                        self.error = str(exc)
                    self.domain.fumaris_liquid.last_error = self.error
                    self.request_stop()
            return True
        if self.future is not None and self._holding():
            return True
        if self.future is not None:
            try:
                result = result_owned(self.future)
                if self.phase == "STARTING":
                    self.domain.fumaris_liquid.runtime_info = json.dumps(
                        {key: result.get(key) for key in ("device", "backend", "protocol")}, ensure_ascii=False)
                elif self.phase == "FRAME":
                    self.inflight.popleft()
                    self.last_response = result[0]
                    display.apply(self.scene, self.domain, *result)
                    shown = result[0]["frame"]
                    if self.bake and shown >= self.last_frame:
                        self.stopping = True
                elif self.phase == "SNAPSHOT":
                    self.last_response = result[0]
                    display.apply(self.scene, self.domain, *result)
                elif self.phase == "CLOSING":
                    self.executor.shutdown(wait=False)
                    if self.error:
                        self.domain.fumaris_liquid.status = "Simulation failed"
                        self.domain.fumaris_liquid.last_error = self.error
                    elif self.bake and self.last_response is not None and \
                            self.last_response.get("frame", -1) >= self.last_frame:
                        # Requests run ahead of the frames received: complete once the last one was cached.
                        self.domain.fumaris_liquid.status = "Bake complete"
                    else:
                        self.domain.fumaris_liquid.status = "Stopped"
                    return False
            except Exception as exc:
                first_error = self.error is None and (not self.stopping or self.phase == "CLOSING")
                if first_error:
                    self.error = str(exc)
                self.stopping = True
                if self.error:
                    self.domain.fumaris_liquid.last_error = self.error
                    if first_error:
                        print("Aquaris:", self.error)
                if self.phase == "CLOSING":
                    self.domain.fumaris_liquid.status = "Cleanup incomplete — retrying…"
                    self._cleanup_retry_at = time.monotonic() + 1
            self.future = None
        if self.stopping:
            if time.monotonic() < self._cleanup_retry_at:
                return True
            self.phase = "CLOSING"
            self._submit(self._close)
            return True
        if self.inflight and (self.paused or self.bridge.share_wanted):
            # Paused, or the shared memory has to grow: take what is in flight first.
            self.phase = "FRAME"
            self._submit(self._receive_next, work=True)
            return True
        if self.paused:
            if self._remesh and self.last_response is not None:
                self._remesh = False
                try:
                    settings = self._settings()
                    update = settings if settings != self.last_settings else None
                    self.last_settings = settings
                    self.phase = "SNAPSHOT"
                    self._submit(self._snapshot, update, self.last_response.get("frame", self.frame - 1),
                                 self.recording or self.domain.fumaris_liquid.display in {"SURFACE", "PREVIEW"}, work=True)
                except Exception as exc:
                    self.domain.fumaris_liquid.runtime_warning = str(exc)
            else:
                self.domain.fumaris_liquid.status = "Paused — solver state retained"
            return True
        try:
            if self.bridge.share_wanted:
                self.bridge.reshare()
            exported = False
            # Two frames in flight: the worker simulates the newer while it meshes and sends the older. A frame's
            # shared payload has been displayed (and cached) before the request after the next one is sent.
            while len(self.inflight) < 2 and not (self.bake and self.frame > self.last_frame):
                self._send_next()
                exported = True
            if exported and shown is not None and not self.bake:
                # Show the scene at the frame of the liquid shown (the exports ran ahead of it).
                self.scene.frame_set(shown)
            if self.inflight:
                self.phase = "FRAME"
                self._submit(self._receive_next, work=True)
        except Exception as exc:
            if self.error is None:
                self.error = str(exc)
            self.domain.fumaris_liquid.last_error = self.error
            self.request_stop()
        return True

    def _settings(self):
        settings = exporters.settings(self.scene, self.domain)
        # A preview that is not recorded is always meshed on the GPU, whatever
        # quality is chosen for the recorded surface.
        if (self.domain.fumaris_liquid.display == "PREVIEW" and not self.recording
                and settings.get("mesh_quality") not in {"GPU", "GPU_SMOOTH"}):
            settings["mesh_quality"] = "GPU_SMOOTH"
        return settings

    def _export_frame(self):
        started = time.perf_counter()
        p = self.domain.fumaris_liquid
        self.scene.frame_set(self.frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        fps = self.scene.render.fps / max(self.scene.render.fps_base, 1e-8)
        dt = p.speed / max(fps, 1e-8)
        header, payload = self.exporter.frame(depsgraph, dt, self.frame)
        # A recording always keeps the full renderable surface regardless of
        # the selected diagnostic preview display.
        # The viewport preview draws the meshed surface itself, so it needs one
        # too; when nothing is recorded that surface never becomes a Blender mesh.
        header["surface"] = self.recording or p.display in {"SURFACE", "PREVIEW"}
        header["normals"] = p.display == "PREVIEW"
        header["_preview_only"] = header["viewport_only"] = p.display == "PREVIEW" and not self.recording
        self.export_ms = (time.perf_counter() - started) * 1000
        settings = self._settings()
        update = settings if settings != self.last_settings else None
        self.last_settings = settings
        return header, payload, update

    def _step(self, header, payload, update, sent=None):
        started = time.perf_counter()
        preview_only = header.pop("_preview_only", False)
        try:
            if update is not None:
                self.bridge.request("update", {"settings": update})
            if sent is not None:
                self.bridge.on_sent = sent.set
            response, result = self.bridge.request("frame", header, payload)
        finally:
            if sent is not None:
                sent.set()
        response["worker_ms"] = (time.perf_counter() - started) * 1000
        response["export_ms"] = self.export_ms
        response["frame"] = header["frame"]
        if preview_only:
            response["preview_only"] = True
        if self.cache is not None:
            self.cache.write(header["frame"], response, result)
        return response, result

    def _snapshot(self, update, frame, surface):
        """The held frame again, meshed with the current surface settings; no time passes."""
        if update is not None:
            self.bridge.request("update", {"settings": update})
        response, result = self.bridge.request("snapshot", {"surface": surface, "frame": frame,
                                                            "normals": self.domain.fumaris_liquid.display == "PREVIEW"})
        response["frame"] = frame
        return response, result

    def request_stop(self):
        if self.stopping:
            return
        self.stopping = True
        self.paused = False
        self._cancel_pending()
        self.domain.fumaris_liquid.status = "Stopping liquid worker…"
        self.stopper = threading.Thread(target=self._stop_worker, daemon=True,
                                        name="FumarisLiquidStop")
        self.stopper.start()

    def _stop_worker(self):
        try:
            self.bridge.close()
        except Exception as exc:
            if self.error is None:
                self.error = str(exc)

    def _close(self):
        if self.stopper is not None and self.stopper.ident is not None:
            self.stopper.join()
        self.bridge.close()
        self._verify_work_complete()
        self._release_cache()

    def _report_cleanup(self, error):
        message = str(error)
        # Historical cleanup details grow on retry; report each leading cause
        # once while that cause remains the same.
        key = message.split("\n", 1)[0]
        if self._last_cleanup_report != key:
            print("Aquaris cleanup incomplete:", message)
            self._last_cleanup_report = key

    def poll(self):
        if self.pipelined:
            return self._poll_pipelined()
        if self.future is not None and not self.future.done():
            if self.phase == "STARTING" and self._prepared is None and not self.stopping:
                # The solver is starting on its thread; sample the scene for the
                # first frame meanwhile instead of after it.
                try:
                    self._prepared = self._export_frame()
                except Exception as exc:
                    if self.error is None:
                        self.error = str(exc)
                    self.domain.fumaris_liquid.last_error = self.error
                    self.request_stop()
            return True
        if self.future is not None and self._holding():
            return True
        if self.future is not None:
            try:
                result = result_owned(self.future)
                if self.phase == "STARTING":
                    self.domain.fumaris_liquid.runtime_info = json.dumps(
                        {key: result.get(key) for key in ("device", "backend", "protocol")}, ensure_ascii=False)
                elif self.phase == "FRAME":
                    self.last_response = result[0]
                    if self.bake and self.frame >= self.last_frame:
                        self.stopping = True
                    self.frame += 1
                    # Start the next frame before showing this one, so the
                    # worker solves it while Blender takes in the mesh instead
                    # of waiting for that. The order the scene and the liquid
                    # are seen in is unchanged: the scene was already set to
                    # the next frame directly after each apply.
                    requested = False
                    if not self.stopping and not self.paused:
                        try:
                            self._request_frame()
                            requested = True
                            # Blender's mesh writes hold the interpreter lock, so
                            # the request would otherwise sit unsent on its thread
                            # until they finish. Wait for it to leave first.
                            self._sent.wait(0.1)
                        except Exception as exc:
                            if self.error is None:
                                self.error = str(exc)
                            self.domain.fumaris_liquid.last_error = self.error
                            self.request_stop()
                    display.apply(self.scene, self.domain, *result)
                    if requested:
                        return True
                elif self.phase == "SNAPSHOT":
                    self.last_response = result[0]
                    display.apply(self.scene, self.domain, *result)
                elif self.phase == "CLOSING":
                    self.executor.shutdown(wait=False)
                    if self.error:
                        self.domain.fumaris_liquid.status = "Simulation failed"
                        self.domain.fumaris_liquid.last_error = self.error
                    elif self.bake and self.frame > self.last_frame:
                        self.domain.fumaris_liquid.status = "Bake complete"
                    else:
                        self.domain.fumaris_liquid.status = "Stopped"
                    return False
            except Exception as exc:
                first_error = self.error is None and (not self.stopping or self.phase == "CLOSING")
                if first_error:
                    self.error = str(exc)
                self.stopping = True
                if self.error:
                    self.domain.fumaris_liquid.last_error = self.error
                    if first_error:
                        print("Aquaris:", self.error)
                if self.phase == "CLOSING":
                    self.domain.fumaris_liquid.status = "Cleanup incomplete — retrying…"
                    self._cleanup_retry_at = time.monotonic() + 1
            self.future = None
        if self.stopping:
            if time.monotonic() < self._cleanup_retry_at:
                return True
            self.phase = "CLOSING"
            # If the pool refuses this submit, _timer's synchronous abort path
            # remains available; cleanup does not depend on a working executor.
            self._submit(self._close)
        elif self.paused and self._remesh and self.last_response is not None:
            # A surface setting changed while paused: show it on the frame the
            # solver is holding instead of waiting for the next one.
            self._remesh = False
            try:
                settings = self._settings()
                update = settings if settings != self.last_settings else None
                self.last_settings = settings
                self.phase = "SNAPSHOT"
                self._submit(self._snapshot, update, self.last_response.get("frame", self.frame - 1),
                             self.recording or self.domain.fumaris_liquid.display in {"SURFACE", "PREVIEW"}, work=True)
            except Exception as exc:
                self.domain.fumaris_liquid.runtime_warning = str(exc)
        elif self.paused:
            self.domain.fumaris_liquid.status = "Paused — solver state retained"
        else:
            try:
                self._request_frame()
            except Exception as exc:
                if self.error is None:
                    self.error = str(exc)
                self.domain.fumaris_liquid.last_error = self.error
                self.request_stop()
        return True

    def abort(self):
        """Close immediately during load/unregister, releasing OS and cache resources."""
        self.stopping = True
        self._cancel_pending()
        try:
            self.bridge.close()
            self.executor.shutdown(wait=True, cancel_futures=True)
            self.bridge.close()
            self._verify_tasks_complete()
            self._release_cache()
        except Exception as exc:
            if self.error is None:
                self.error = str(exc)
            raise


def surface_changed(domain):
    """A surface setting of this domain was edited; a paused session re-meshes its held frame."""
    if _active is not None and _active.domain == domain and _active.paused and not _active.stopping:
        _active._remesh = True


def _timer():
    global _active
    if _active is None:
        return None
    redraw = (_active.future is None or _active.future.done()) and _active.wait_seconds() == 0  # (not while a frame is held)
    aborting = False
    try:
        if _active.domain.name not in _active.scene.objects:
            aborting = True
            _active.abort()
            _active = None
            return None
        if bpy.context.scene != _active.scene:
            _active.request_stop()
        if not _active.poll():
            _active = None
            return None
    except Exception as exc:
        message = str(exc)
        if _active.error is None:
            _active.error = message
            print("Aquaris session:", message)
        if aborting:
            _active._report_cleanup(exc)
            return 1.0
        try:
            _active.abort()
        except Exception as cleanup:
            _active._report_cleanup(cleanup)
            return 1.0
        _active = None
        return None
    if redraw:
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type in {"VIEW_3D", "PROPERTIES"}:
                        area.tag_redraw()
        except (ReferenceError, RuntimeError):
            pass  # A removed UI area cannot stop polling an owned task/cache.
    if _active.paused or _active.stopping:
        return 0.05
    # a frame held for Preview Rate: come back when it is due
    wait = _active.wait_seconds()
    return min(max(wait, 0.001), 0.05) if wait > 0 else 0.001


def start(scene, domain, bake=False):
    global _active
    if _active is not None:
        raise RuntimeError("Stop the active liquid simulation first")
    owner = Session(scene, domain, bake)
    _active = owner
    _shown_frames.clear()  # The solver now decides what is displayed.
    try:
        if not bpy.app.timers.is_registered(_timer):
            bpy.app.timers.register(_timer, first_interval=0.02)
        owner.begin()
    except BaseException as primary:
        # Interrupted partial startup must never resume as a normal frame.
        owner.stopping = True
        owner._cancel_pending()
        if owner.error is None:
            owner.error = str(primary)
            if not isinstance(primary, Exception) and not owner.error:
                owner.error = type(primary).__name__
        if not isinstance(primary, Exception):
            raise  # Keep direct interrupt semantics and the retained timer owner.
        try:
            owner.abort()
        except Exception as cleanup:
            # The global owner remains available to the timer/shutdown even if
            # submission never returned a Future or cache cleanup is uncertain.
            owner._report_cleanup(cleanup)
            raise primary from cleanup
        _active = None
        if bpy.app.timers.is_registered(_timer):
            bpy.app.timers.unregister(_timer)
        raise
    return owner


def shutdown():
    global _active
    if _active is not None:
        _active.abort()
        _active = None
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)


@persistent
def _load_pre(_):
    _render_end()
    shutdown()
    display.clear_runtime()
    from . import preview
    preview.clear()


@persistent
def _load_post(_):
    for obj in bpy.data.objects:
        if obj.fumaris_liquid.role == "DOMAIN":
            obj.fumaris_liquid.status = "Ready — no running solver"
            obj.fumaris_liquid.last_error = ""
            obj.fumaris_liquid.runtime_info = ""
            obj.fumaris_liquid.runtime_warning = ""
            p = obj.fumaris_liquid
            if not p.is_property_set("mesh_cell_relative") and p.is_property_set("mesh_cell_size"):
                # Saved before the cell followed the radius: keep the detail it had.
                p.mesh_cell_factor = min(4.0, max(0.5, p.mesh_cell_size / max(p.particle_radius, 1e-6)))
                p.mesh_cell_relative = True
    _frame_change(bpy.context.scene)


def _show_cached(scene, frame):
    global _playback_guard
    _playback_guard = True
    try:
        for domain in scene.objects:
            p = domain.fumaris_liquid
            if p.role != "DOMAIN" or not p.cache_playback or not p.domain_id:
                continue
            try:
                header, payload = cache_for(domain).read(frame)
                display.apply(scene, domain, header, payload)
            except FileNotFoundError:
                # Do not display a different cached frame while rendering a gap.
                display.clear(domain)
                p.status = f"No cache for frame {frame}"
            except Exception as exc:
                p.last_error = str(exc)
        _shown_frames[scene.as_pointer()] = frame
    finally:
        _playback_guard = False


def _nearest_frame(scene):
    return scene.frame_current + (1 if scene.frame_subframe >= 0.5 else 0)


@persistent
def _frame_change(scene, depsgraph=None):
    if _active is not None or _playback_guard:
        return
    key = scene.as_pointer()
    rendering = _render_frames.get(key)
    if rendering is not None:
        # A motion-blurred render evaluates the scene at the shutter's open and
        # close times. Loading the neighbouring frame there swaps the surface
        # for one with other topology in the middle of the renderer's export
        # (measured: 110 s instead of 3 s for four Cycles samples). The frame
        # being rendered stays; its motion comes from the velocity attribute.
        # The next animation frame announces itself through render_pre first,
        # so anything within a shutter and a frame of this one is such a step.
        if abs(scene.frame_current + scene.frame_subframe - rendering) <= scene.render.motion_blur_shutter + 1.0:
            return
        _render_frames.pop(key, None)  # A render that never reported its end.
    if scene.frame_subframe:
        # Between frames the nearer cached frame is shown, and kept if it is
        # already there. A scene rendered through another scene's compositor
        # announces no render, but its shutter times still land here.
        frame = _nearest_frame(scene)
        if _shown_frames.get(key) != frame:
            _show_cached(scene, frame)
        return
    _show_cached(scene, scene.frame_current)


@persistent
def _render_pre(scene, *_):
    if _active is not None or _playback_guard:
        return
    key = scene.as_pointer()
    _render_frames.pop(key, None)
    _show_cached(scene, _nearest_frame(scene))
    _render_frames[key] = scene.frame_current + scene.frame_subframe


@persistent
def _render_end(scene=None, *_):
    if scene is None:
        _render_frames.clear()
        _shown_frames.clear()
    else:
        _render_frames.pop(scene.as_pointer(), None)


def register():
    for handlers, function in ((bpy.app.handlers.load_pre, _load_pre),
                               (bpy.app.handlers.load_post, _load_post),
                               (bpy.app.handlers.frame_change_pre, _frame_change),
                               (bpy.app.handlers.render_pre, _render_pre),
                               (bpy.app.handlers.render_post, _render_end),
                               (bpy.app.handlers.render_cancel, _render_end),
                               (bpy.app.handlers.render_complete, _render_end)):
        if function not in handlers:
            handlers.append(function)


def unregister():
    shutdown()
    display.clear_runtime()
    for handlers, function in ((bpy.app.handlers.load_pre, _load_pre),
                               (bpy.app.handlers.load_post, _load_post),
                               (bpy.app.handlers.frame_change_pre, _frame_change),
                               (bpy.app.handlers.render_pre, _render_pre),
                               (bpy.app.handlers.render_post, _render_end),
                               (bpy.app.handlers.render_cancel, _render_end),
                               (bpy.app.handlers.render_complete, _render_end)):
        if function in handlers:
            handlers.remove(function)
