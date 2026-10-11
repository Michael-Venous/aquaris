"""Owned, nonblocking reconstruction using the native CPU mesher."""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
import time
import bpy
import numpy as np
from .bridge import Bridge
from ._tasks import result_owned, submit_owned

_jobs = {}
_pending = {}
_executor = None
_shutdown_requested = False
_shutdown_report = None


@dataclass
class _Intent:
    scene: object
    domain: object
    header: dict
    payload: bytes
    snapshot: object
    positions: object
    settings: dict


@dataclass
class _Job:
    scene: object
    domain: object
    header: dict
    payload: bytes
    snapshot: object
    bridge: object
    positions: object
    settings: dict
    future: Future = field(default_factory=Future)
    cancelled: bool = False
    closed_verified: bool = False
    result_checked: bool = False
    result: object = None
    build_error: object = None
    submit_error: object = None
    cleanup_error: object = None
    next_retry: float = 0
    last_report: object = None


def _build(job):
    primary = None
    try:
        job.bridge.launch()
        data = np.ascontiguousarray(job.positions, dtype='<f4').tobytes()
        return job.bridge.request('mesh', dict(job.settings, blocks={
            'positions': {'offset': 0, 'count': len(job.positions)}}), data)
    except BaseException as exc:
        primary = exc
        job.build_error = exc
        raise
    finally:
        try:
            job.bridge.close()
            job.closed_verified = True
        except Exception as cleanup:
            job.cleanup_error = cleanup
            job.next_retry = time.monotonic() + 1
            if primary is not None:
                raise primary from cleanup
            raise


def _ensure_timer():
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=.01)


def _remove(key, job):
    if _jobs.get(key) is job:
        _jobs.pop(key, None)


def _report(job, error):
    message = str(error)
    key = message.split('\n', 1)[0]
    if job.last_report != key:
        try:
            print('Aquaris surface:', message)
        except Exception:
            pass
        job.last_report = key
    try:
        primary = job.submit_error or job.build_error
        job.domain.fumaris_liquid.last_error = 'Surface reconstruction failed: ' + str(primary or error)
        if job.cleanup_error is not None:
            job.domain.fumaris_liquid.status = 'Surface cleanup incomplete — retrying…'
    except Exception:
        pass  # Reporting is best effort; it cannot discard a cleanup owner.


def _close_job(job, force=False):
    if job.closed_verified:
        return True
    if not force and time.monotonic() < job.next_retry:
        return False
    try:
        job.bridge.close()
    except Exception as exc:
        job.cleanup_error = exc
        job.next_retry = time.monotonic() + 1
        _report(job, exc)
        return False
    job.closed_verified = True
    return True


def _launch(key, intent):
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='AquarisSurface')
    job = _Job(intent.scene, intent.domain, intent.header, intent.payload, intent.snapshot,
               Bridge(), intent.positions, intent.settings)
    _jobs[key] = job
    try:
        _ensure_timer()
        intent.domain.fumaris_liquid.status = 'Reconstructing surface…'
        submit_owned(_executor, job.future, _build, job)
    except BaseException as exc:
        job.submit_error = exc
        job.cancelled = True
        job.future.cancel()
        _report(job, exc)
        if _close_job(job, force=True) and job.future.done():
            _remove(key, job)
        raise


def request(scene, domain, header, payload):
    from . import display
    if _shutdown_requested:
        raise RuntimeError('Surface worker shutdown is incomplete; retry cleanup first')
    key = domain.as_pointer()
    p = domain.fumaris_liquid
    settings = {'particle_radius': header.get('particle_radius', p.particle_radius),
                'mesh_cell_size': p.mesh_cell_size, 'max_mesh_cells': p.max_mesh_cells,
                'mesh_quality': getattr(p, 'mesh_quality', 'FAST')}
    positions = np.array(display.block(header, payload, 'positions'))
    intent = _Intent(scene, domain, header, payload, display._last_outputs.get(key), positions, settings)
    # Preflight above does not cancel healthy work. New intent owns no child.
    _pending.pop(key, None)
    job = _jobs.get(key)
    if job is not None:
        job.cancelled = True
        job.future.cancel()
        if _close_job(job, force=True) and job.future.done():
            _remove(key, job)
    if key in _jobs:
        _pending[key] = intent  # Latest intent replaces only another intent.
        _ensure_timer()
        if job.closed_verified:
            p.status = 'Waiting for previous surface task to stop…'
        return
    _launch(key, intent)


def _poll():
    global _shutdown_report
    from . import display
    if _shutdown_requested:
        try:
            shutdown()
        except Exception as exc:
            key = str(exc).split('\n', 1)[0]
            if _shutdown_report != key:
                try:
                    print('Aquaris surface shutdown incomplete:', str(exc))
                except Exception:
                    pass
                _shutdown_report = key
            return 1.0
        return None
    for key, job in list(_jobs.items()):
        if not job.future.done():
            if job.cancelled:
                _close_job(job)
            continue
        if not job.result_checked:
            try:
                job.result = result_owned(job.future)
            except Exception as exc:
                if not job.cancelled:
                    if job.build_error is None:
                        job.build_error = exc
                    _report(job, exc)
            job.result_checked = True
        if not _close_job(job):
            continue
        try:
            if (not job.cancelled and job.build_error is None and job.submit_error is None
                    and display._last_outputs.get(key) is job.snapshot):
                response, data = job.result
                blocks = dict(job.header.get('blocks', {}))
                for name, item in response['blocks'].items():
                    blocks[name] = {'offset': len(job.payload) + item['offset'], 'count': item['count']}
                result = dict(job.header, blocks=blocks, surface=True)
                for name in ('mesh_quality', 'meshing_ms'):
                    if name in response:
                        result[name] = response[name]
                display.apply(job.scene, job.domain, result, job.payload + data)
                for window in bpy.context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type == 'VIEW_3D':
                            area.tag_redraw()
        except ReferenceError:
            pass
        except Exception as exc:
            _report(job, exc)
        finally:
            # Task terminality and verified Bridge closure precede removal.
            _remove(key, job)
        # Display callbacks can synchronously install a replacement owner.
        intent = _pending.pop(key, None) if key not in _jobs else None
        if intent is not None:
            try:
                if (display._last_outputs.get(key) is intent.snapshot
                        and intent.domain.name in intent.scene.objects):
                    _launch(key, intent)
            except ReferenceError:
                pass
            except Exception:
                # _launch reports and retains any unverified new owner.
                pass
    return .01 if _jobs or _pending else None


def cancel(domain):
    key = domain.as_pointer()
    _pending.pop(key, None)
    job = _jobs.get(key)
    if job is None:
        return
    job.cancelled = True
    job.future.cancel()
    closed = _close_job(job, force=True)
    if closed and job.future.done():
        _remove(key, job)
    else:
        _ensure_timer()
    if not closed:
        raise RuntimeError('Surface worker cleanup incomplete; reconstruction owner retained') from job.cleanup_error


def shutdown():
    global _executor, _shutdown_requested, _shutdown_report
    _shutdown_requested = True
    _pending.clear()
    failed = []
    for job in list(_jobs.values()):
        job.cancelled = True
        # Settle independent pending tickets before the executor can discard
        # its own queued wrapper Futures without invoking them.
        job.future.cancel()
        if not _close_job(job, force=True):
            failed.append(job)
    if failed:
        _ensure_timer()
        raise RuntimeError('Surface worker cleanup incomplete; all remaining owners retained') from failed[0].cleanup_error
    try:
        if _executor is not None:
            _executor.shutdown(wait=True, cancel_futures=True)
        if any(not job.future.done() for job in _jobs.values()):
            raise RuntimeError('Surface task completion not verified; ownership retained')
    except Exception:
        _ensure_timer()
        raise
    _jobs.clear()
    _executor = None
    _shutdown_requested = False
    _shutdown_report = None
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
