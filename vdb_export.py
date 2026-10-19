"""OpenVDB export of a bake: one .vdb a cached frame, for other packages and renderers.

Grids, in world space:
  surface   level set of the cached surface mesh on voxels of the surface cell (narrow band of three, negative inside)
  density   share of a voxel the liquid particles fill (each counts as a seeded particle's cube of two radii), 0..1,
            on voxels of two radii (finer ones would each be filled by a single particle)
  velocity  mean velocity of the particles in a voxel [m/s, scene units], on the density's voxels
Uses Blender's bundled `openvdb` module; nothing is simulated or meshed again.
"""
from pathlib import Path

import numpy as np

from . import protocol


def _openvdb():
    try:
        import openvdb
    except ImportError as exc:  # Blender builds without OpenVDB
        raise RuntimeError("This Blender has no OpenVDB module; VDB export is unavailable") from exc
    return openvdb


def _array(header, payload, name, width=3):
    data = np.asarray(protocol.unpack_array(header, payload, name), np.float32)
    return data.reshape(-1, width) if width > 1 else data


def frame_grids(header, payload, voxel, particle_radius):
    """The grids of one cached frame (a list, possibly empty)."""
    vdb = _openvdb()
    transform = vdb.createLinearTransform(voxelSize=float(voxel))
    grids = []
    vertices = _array(header, payload, "vertices")
    triangles = np.asarray(protocol.unpack_array(header, payload, "triangles"), np.int32).reshape(-1, 3)
    if len(triangles):
        surface = vdb.FloatGrid.createLevelSetFromPolygons(vertices.astype(np.float32), triangles=triangles.astype(np.uint32),
                                                           transform=transform, halfWidth=3.0)
        surface.name = "surface"
        grids.append(surface)
    positions = _array(header, payload, "positions")
    voxel = max(float(voxel), 2.0 * float(particle_radius))
    transform = vdb.createLinearTransform(voxelSize=voxel)
    if len(positions):
        velocities = _array(header, payload, "velocities")
        # Each particle spread over the eight voxels around it with trilinear weights: inside the liquid a voxel
        # gathers about one particle's worth, at the surface a share (counting whole particles a voxel made every
        # occupied voxel full).
        grid = positions / voxel - 0.5
        base = np.floor(grid).astype(np.int64)
        frac = (grid - base).astype(np.float32)
        lo = base.min(axis=0)
        dims = base.max(axis=0) - lo + 2
        size = int(np.prod(dims))
        weight = np.zeros(size, np.float32)
        moving = np.zeros((size, 3), np.float32)
        have = len(velocities) == len(positions)
        for corner in range(8):
            offset = np.array([corner & 1, (corner >> 1) & 1, (corner >> 2) & 1])
            w = np.prod(np.where(offset, frac, 1.0 - frac), axis=1)
            flat = np.ravel_multi_index((base - lo + offset).T, dims)
            weight += np.bincount(flat, weights=w, minlength=size).astype(np.float32)
            if have:
                for axis in range(3):
                    moving[:, axis] += np.bincount(flat, weights=w * velocities[:, axis], minlength=size)
        # A particle stands for a cube of two radii (the seeding spacing): the share of the voxel it fills.
        share = (2.0 * particle_radius / voxel) ** 3
        density = vdb.FloatGrid()
        density.copyFromArray(np.minimum(weight * share, 1.0).reshape(dims), ijk=tuple(int(v) for v in lo), tolerance=1e-4)
        density.transform = transform
        density.name = "density"
        grids.append(density)
        if have:
            filled = weight > 1e-6
            moving[filled] /= weight[filled, None]
            moving[~filled] = 0
            velocity = vdb.Vec3SGrid()
            velocity.copyFromArray(moving.reshape(tuple(dims) + (3,)), ijk=tuple(int(v) for v in lo), tolerance=(1e-6, 1e-6, 1e-6))
            velocity.transform = transform
            velocity.name = "velocity"
            grids.append(velocity)
    return grids


def export(cache, directory, voxel, particle_radius, frames=None, progress=None):
    """Write <directory>/liquid_<frame>.vdb for every cached frame (or `frames`); returns the paths written."""
    vdb = _openvdb()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    chosen = cache.frames() if frames is None else list(frames)
    for done, frame in enumerate(chosen):
        header, payload = cache.read(frame)
        grids = frame_grids(header, payload, voxel, particle_radius)
        path = directory / f"liquid_{frame:04d}.vdb"
        if grids:
            vdb.write(str(path), grids=grids)
            written.append(path)
        if progress is not None:
            progress(done + 1, len(chosen))
    return written
