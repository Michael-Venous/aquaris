"""Rebuild the surface of a finished bake with the current surface settings, without simulating again.

The particles, their velocities and numbers, and the whitewater a bake stored stay exactly as they are; only
the meshed surface (vertices, triangles, vertex velocities and normals) of each cached frame is made again by
the worker's mesh action and written back into that frame. One frame a step, so a modal operator can show
progress and stop between frames.
"""
from .bridge import Bridge
from . import exporters, protocol, session

SURFACE_BLOCKS = ("vertices", "triangles", "vertex_velocities", "vertex_normals")


def _blocks(header, payload):
    """{name: raw little-endian bytes} of every block in a frame."""
    out = {}
    for name, block in header.get("blocks", {}).items():
        width = protocol.width(name)
        start, count = int(block["offset"]), int(block["count"])
        end = start + count * width * 4
        if start < 0 or count < 0 or end > len(payload):
            raise RuntimeError(f"Liquid cache frame has an invalid block: {name}")
        out[name] = bytes(payload[start:end])
    return out


def _pack(blocks):
    header, payload = {}, bytearray()
    for name, data in blocks.items():
        width = protocol.width(name)
        header[name] = {"offset": len(payload), "count": len(data) // (4 * width)}
        payload.extend(data)
    return header, bytes(payload)


class Rebuild:
    """Owns the cache slot's lock and a worker for the time of one rebuild."""

    def __init__(self, scene, domain):
        if session.active() is not None:
            raise RuntimeError("Stop the running simulation before rebuilding the surface")
        p = domain.fumaris_liquid
        self.cache = session.cache_for(domain)
        self.frames = self.cache.frames()
        if not self.frames:
            raise RuntimeError("Nothing is baked in this cache slot")
        settings = exporters.settings(scene, domain)
        self.request = {name: settings[name] for name in ("particle_radius", "mesh_cell_size", "max_mesh_cells", "mesh_quality",
                                                          "droplet_scale", "surface_smoothing")}
        # Stored vertex velocities are scaled for the renderer as a live frame's are (exporters: dt * fps).
        self.request["motion_scale"] = p.speed
        self.velocities = p.surface_velocity
        self.done = 0
        self.cache.acquire()
        self.bridge = Bridge()
        try:
            self.bridge.launch()
        except Exception:
            self.cache.release()
            raise

    def step(self):
        """Rebuild the next frame; returns True when every frame is done."""
        frame = self.frames[self.done]
        header, payload = self.cache.read(frame)
        stored = _blocks(header, payload)
        sent = {"positions": stored.get("positions", b"")}
        if self.velocities and "velocities" in stored and len(stored["velocities"]) == len(sent["positions"]):
            sent["velocities"] = stored["velocities"]
        blocks, data = protocol.pack_arrays(**sent)
        response, result = self.bridge.request("mesh", dict(self.request, blocks=blocks), data)
        meshed = _blocks(response, result)
        kept = {name: value for name, value in stored.items() if name not in SURFACE_BLOCKS}
        kept.update({name: meshed[name] for name in SURFACE_BLOCKS if name in meshed})
        new_blocks, new_payload = _pack(kept)
        header = dict(header, blocks=new_blocks, mesh_quality=response.get("mesh_quality"), surface=True,
                      mesh_fallback=response.get("mesh_fallback", ""), meshing_ms=response.get("meshing_ms", 0))
        header.pop("preview_only", None)
        self.cache.write(frame, header, new_payload)
        self.done += 1
        return self.done >= len(self.frames)

    def close(self):
        try:
            self.bridge.close()
        finally:
            self.cache.release()
