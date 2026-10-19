"""Evaluate Blender participants on the main thread, then serialize owned arrays.

Closed meshes are filled by parity testing their evaluated triangles. Collider
triangles are sampled across their area, including interiors, so large polygons
do not leave vertex-only holes. Temporary evaluated meshes are always released.
"""

import math
from collections import Counter

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from . import masks, volume_export


def participants(scene, domain):
    props = domain.fumaris_liquid
    seen = set()
    for role, collection in (
        ("EMITTER", props.emitter_collection),
        ("COLLIDER", props.collider_collection),
        ("EFFECTOR", props.effector_collection),
        ("OUTFLOW", props.outflow_collection),
    ):
        objects = collection.all_objects if collection else ()
        for obj in sorted(objects, key=lambda value: value.name):
            if obj.as_pointer() in seen or obj == domain:
                continue
            if obj.fumaris_liquid.role == role:
                seen.add(obj.as_pointer())
                yield obj


# Four million particles is what the surface path can follow: a one-radius
# cell needs 9-14 surface nodes per particle against the 64 million cell cap.
PARTICLE_LIMIT, BOUNDARY_LIMIT, MESH_CELL_LIMIT = 4_000_000, 4_000_000, 64_000_000
# Extent of the slabs that remove escaped liquid [m].
STRAY_REACH = 1.0e4


def limits(p):
    """(particles, collider samples, surface cells) this domain may use.

    The solver allocates as the simulation grows, so the automatic limits cost
    nothing until they are reached; the fixed budgets remain for users who
    want an early stop.
    """
    if p.auto_budgets:
        return PARTICLE_LIMIT, BOUNDARY_LIMIT, MESH_CELL_LIMIT
    return p.max_particles, p.max_boundary, p.max_mesh_cells


def mesh_cell(p):
    """Surface cell size: a multiple of the particle radius unless set by hand."""
    return max(0.005, p.particle_radius * p.mesh_cell_factor) if p.mesh_cell_relative else p.mesh_cell_size


def _over(p, what, advice):
    if p.auto_budgets:
        return f"{what} exceeds what the solver supports; {advice}"
    return f"{what} exceeds its budget; {advice}, raise the budget or turn on Automatic Limits"


def settings(scene, domain):
    p = domain.fumaris_liquid
    if any(p.bounds_max[i] <= p.bounds_min[i] for i in range(3)):
        raise RuntimeError("Each domain maximum must exceed its minimum")
    scale = max(1e-8, scene.unit_settings.scale_length)
    return {
        "cfl": p.step_size,
        "flip_blend": p.flip_ratio,
        # m²/s into the scene's own length unit, as gravity is
        "viscosity": p.viscosity / scale ** 2,
        # N/m over water's density (m³/s²), into the scene's length unit
        "surface_tension": p.surface_tension / 1000.0 / scale ** 3,
        "particle_radius": p.particle_radius,
        "substeps": p.substeps,
        "max_particles": limits(p)[0],
        "max_boundary": limits(p)[1],
        "gravity": [v / scale for v in p.gravity],
        "bounds_min": list(p.bounds_min),
        "bounds_max": list(p.bounds_max),
        "mesh_cell_size": mesh_cell(p),
        "mesh_quality": p.mesh_quality,
        "droplet_scale": p.droplet_scale,
        "surface_smoothing": p.surface_smoothing,
        "surface_velocity": p.surface_velocity,
        "max_mesh_cells": limits(p)[2],
        "whitewater_generate": p.whitewater_generate,
        "whitewater_amount": p.whitewater_amount,
        "whitewater_lifetime": p.whitewater_lifetime,
        "whitewater_buoyancy": p.whitewater_buoyancy,
        "whitewater_drag": p.whitewater_drag,
        "max_whitewater": p.max_whitewater,
    }


def _merged_normals(normals, group, count):
    """The normal of each merged collider sample: the normalised sum of the face normals of every sample merged into
    it. A sample on an edge two faces share stands for both; keeping the normal of whichever duplicate came first
    made two bakes of the same scene differ (Blender's evaluated geometry does not come out in a fixed order), and
    with them the liquid that meets that edge."""
    summed = np.zeros((count, 3), dtype=np.float64)
    for axis in range(3):
        summed[:, axis] = np.bincount(group, weights=normals[:, axis], minlength=count)
    length = np.linalg.norm(summed, axis=1, keepdims=True)
    return (summed / np.maximum(length, 1e-30)).astype(normals.dtype)


def domain_walls(p):
    """The closed sides of the region as one mesh, faces towards the inside (a collider's geometry tuple).

    Each side reaches a few particle radii past the region's edges, so that two
    closed sides meet without a gap along their common edge.
    """
    low, high = np.array(p.bounds_min, dtype=np.float64), np.array(p.bounds_max, dtype=np.float64)
    reach = 4.0 * p.particle_radius
    vertices, triangles = [], []
    for axis in range(3):
        u, v = (axis + 1) % 3, (axis + 2) % 3
        for side, name in ((0, "MIN"), (1, "MAX")):
            if "XYZ"[axis] + "_" + name not in p.domain_walls:
                continue
            plane = high[axis] if side else low[axis]
            corners = []
            for a, b in ((0, 0), (1, 0), (1, 1), (0, 1)):
                point = np.empty(3)
                point[axis] = plane
                point[u] = (high[u] + reach) if a else (low[u] - reach)
                point[v] = (high[v] + reach) if b else (low[v] - reach)
                corners.append(point)
            first = len(vertices)
            vertices += corners
            # (u, v, axis) is right-handed: counter-clockwise in (u, v) faces +axis, the inside of the minimum side.
            quad = [(0, 1, 2), (0, 2, 3)] if not side else [(0, 2, 1), (0, 3, 2)]
            triangles += [[first + i for i in t] for t in quad]
    return (np.array(vertices, dtype=np.float64).reshape(-1, 3), np.array(triangles, dtype=np.int32).reshape(-1, 3),
            np.identity(4), None, None)


def pack_blocks(arrays):
    blocks = {}
    payload = bytearray()
    for name, array in arrays.items():
        array = np.ascontiguousarray(array, dtype="<f4").reshape((-1, 3))
        blocks[name] = {"offset": len(payload), "count": len(array)}
        payload.extend(array.tobytes())
    return blocks, bytes(payload)


def _attribute(data, name, vector=False):
    if not name or not hasattr(data, "attributes"):
        return None
    attr = data.attributes.get(name)
    if attr is None or attr.domain != "POINT":
        return None
    result = np.empty((len(attr.data), 3) if vector else len(attr.data), dtype=np.float32)
    try:
        attr.data.foreach_get("vector" if vector else "value", result.ravel())
    except (TypeError, AttributeError, ValueError):
        return None
    return result


def _inside(bvh, position, epsilon, max_distance):
    direction = Vector((0.812381, 0.331277, 0.480129)).normalized()
    origin = Vector(position)
    hits = 0
    # A closed mesh can be concave or contain cavities; count all crossings.
    for _ in range(512):
        hit, normal, index, distance = bvh.ray_cast(origin, direction, max_distance)
        if hit is None:
            return bool(hits % 2)
        hits += 1
        origin = hit + direction * epsilon
    raise RuntimeError("Mesh has more than 512 crossings; simplify the emission volume")


class SceneExporter:
    def __init__(self, scene, domain, *, boundary_motion_from_velocity=True):
        self.boundary_motion_from_velocity = bool(boundary_motion_from_velocity)
        self.birth_solid_cache = {}
        self.birth_motion_cache = {}
        self.source_volume_cache = {}
        self.source_volume_active = set()
        self.source_motion_cache = {}
        self.source_phase = {}
        self.source_phase_updates = {}
        self.source_phase_frames = {}
        self.scene = scene
        self.domain = domain
        self.previous = {}
        self.boundary_cache = {}
        # Outward face normal of every collider sample, in the order _boundary returned them.
        self.boundary_normals = {}
        # Per collider: where each surface sample sits on its triangle, reused while the mesh keeps its shape.
        self.boundary_lattice = {}
        self.initialized = set()
        # Box around everything the liquid has been able to touch so far; see _strays.
        self.reach = None
        self.next_emission = {}
        self.time = 0.0
        self.image_cache = {}
        self.mask_frame = None
        masks.clear()  # Temporary sequence images of an earlier run.
        self.last_boundary_arrays = None
        # Colliders found unmoved this frame, their zero velocities, and what the
        # last frame's collider arrays were built from; see frame().
        self.boundary_unmoved = set()
        self.boundary_still = {}
        self.last_boundary_parts = None
        self.solid_cache = {}

    def _solid(self, key, geometry):
        world, triangles = geometry[:2]
        if not len(triangles):
            return None
        cached = self.solid_cache.get(key)
        same_topology = cached is not None and np.array_equal(cached[1], triangles)
        if same_topology and np.array_equal(cached[0], world):
            return cached[2:]
        if same_topology:
            closed = cached[3]
        else:
            edges = Counter(tuple(sorted((int(t[i]), int(t[(i+1)%3]))))
                            for t in triangles for i in range(3))
            closed = bool(edges) and all(count == 2 for count in edges.values())
        bvh = BVHTree.FromPolygons([Vector(v) for v in world], triangles.tolist(), all_triangles=True)
        minimum, maximum = world.min(axis=0), world.max(axis=0)
        distance = float(np.linalg.norm(maximum-minimum)*2+1)
        self.solid_cache[key] = (world, triangles, bvh, closed, minimum, maximum, distance)
        return bvh, closed, minimum, maximum, distance

    def _clip_solids(self, positions, velocities, solids, margin):
        if not len(positions):
            return positions, velocities, 0
        keep = np.ones(len(positions), dtype=bool)
        epsilon = max(1e-7, margin*1e-4)
        for bvh, closed, minimum, maximum, distance in solids:
            candidates = np.flatnonzero(keep & np.all((positions >= minimum-margin) &
                                                      (positions <= maximum+margin), axis=1))
            for index in candidates:
                position = positions[index]
                hit, normal, face, nearest = bvh.find_nearest(Vector(position), margin)
                # A particle closer to a collider's face than its own radius
                # would be in it: the grid solver's colliders are one-sided
                # faces at their samples, so liquid may be born touching one.
                # (2 r, from the removed particle solver's sampled shell, left
                # a gap under every pool, which it fell through at the start.)
                # Closed meshes also reject arbitrarily deep interior source
                # samples; open meshes only reject points close to their surface.
                if hit is not None or (closed and _inside(bvh, position, epsilon, distance)):
                    keep[index] = False
        excluded = int(np.count_nonzero(~keep))
        return positions[keep], velocities[keep], excluded

    def _clip_birth_solids(self, positions, velocities, collider_geometry,
                           end_solids, margin, birth_time):
        if not self.boundary_motion_from_velocity or birth_time >= self.dt:
            return self._clip_solids(positions, velocities, end_solids, margin)
        if not len(positions):
            return positions, velocities, 0
        alpha = max(0.0, min(1.0, birth_time / self.dt))
        solids = []
        for geometries in collider_geometry.values():
            for key, geometry in geometries:
                world, triangles = geometry[:2]
                end_cache = self.solid_cache.get(key)
                if end_cache is None:
                    continue
                if key not in self.birth_motion_cache:
                    previous = self.previous.get(key)
                    matching = (previous is not None and previous[0].shape == world.shape
                                and np.array_equal(previous[2], triangles))
                    # Classify once per frame, including static high-poly solids.
                    self.birth_motion_cache[key] = (previous[0] if matching and
                        not np.array_equal(previous[0], world) else None)
                old_world = self.birth_motion_cache[key]
                if old_world is None:
                    # Static/new/incompatible geometry has zero chord motion.
                    solids.append(end_cache[2:])
                    continue
                cached = self.birth_solid_cache.get(key)
                if cached is not None and cached[0] == alpha:
                    solids.append(cached[1])
                    continue
                birth_world = old_world + (world - old_world) * alpha
                minimum, maximum = birth_world.min(axis=0), birth_world.max(axis=0)
                candidates = np.all((positions >= minimum-margin) &
                                    (positions <= maximum+margin), axis=1)
                if not np.any(candidates):
                    continue
                bvh = BVHTree.FromPolygons([Vector(v) for v in birth_world],
                                          triangles.tolist(), all_triangles=True)
                distance = float(np.linalg.norm(maximum-minimum)*2+1)
                solid = (bvh, end_cache[3], minimum, maximum, distance)
                # At most one intermediate BVH per collider, regardless of ticks.
                self.birth_solid_cache[key] = (alpha, solid)
                solids.append(solid)
        return self._clip_solids(positions, velocities, solids, margin)

    def _geometry(self, obj, depsgraph, props=None, matrix=None):
        evaluated = obj if obj.is_evaluated else obj.evaluated_get(depsgraph)
        props = props or obj.fumaris_liquid
        mesh = None
        try:
            # Asking Blender to preserve all layers with a depsgraph re-evaluates
            # anonymous GN instance objects as their empty original instancer.
            # Their already evaluated prototype mesh must be copied directly.
            mesh = evaluated.to_mesh() if matrix is not None else evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            if mesh is None or not len(mesh.vertices):
                raise RuntimeError(f"{obj.name}: no evaluated mesh geometry")
            mesh.calc_loop_triangles()
            local = np.empty((len(mesh.vertices), 3), dtype=np.float64)
            mesh.vertices.foreach_get("co", local.ravel())
            matrix = np.asarray(matrix if matrix is not None else evaluated.matrix_world, dtype=np.float64)
            world = local @ matrix[:3, :3].T + matrix[:3, 3]
            triangles = np.asarray([tuple(t.vertices) for t in mesh.loop_triangles], dtype=np.int32).reshape((-1, 3))
            return world, triangles, matrix, _attribute(mesh, props.mask_attribute), _attribute(mesh, props.velocity_attribute, True)
        finally:
            if mesh is not None:
                evaluated.to_mesh_clear()

    def _instances(self, obj, props, depsgraph):
        result = []
        for instance in depsgraph.object_instances:
            if not instance.is_instance or instance.parent is None or instance.parent.original != obj:
                continue
            key = (obj.as_pointer(), "instance", tuple(instance.persistent_id))
            try:
                geometry = self._geometry(instance.object, depsgraph, props, instance.matrix_world.copy())
            except (RuntimeError, TypeError):
                continue
            result.append((key, geometry))
        return sorted(result, key=lambda item: item[0])

    def _budget(self, count):
        if count > limits(self.domain.fumaris_liquid)[0]:
            raise RuntimeError(_over(self.domain.fumaris_liquid, "Emitter sampling", "increase the particle radius"))

    def _volume(self, obj, props, geometry, spacing, history_key=None, sample_time=None):
        motion_geometry = geometry
        history_key = history_key if history_key is not None else obj.as_pointer()
        translation = self._source_translation(history_key, geometry)
        if (self.boundary_motion_from_velocity and sample_time is not None and
                translation is not None and np.any(translation != 0)):
            # A continuous translated volume occupies its material chord pose
            # at the actual birth tick, not the future frame-end cap.
            alpha = max(0.0, min(1.0, (sample_time-self.time) / self.dt))
            offset = translation * (1-alpha)
            matrix = geometry[2].copy()
            matrix[:3, 3] -= offset
            geometry = (geometry[0]-offset, geometry[1], matrix, geometry[3], geometry[4])
        world, triangles, matrix, mask, attr_velocity = geometry
        inverse = np.linalg.inv(matrix)
        local = (world - matrix[:3, 3]) @ inverse[:3, :3].T
        local_min = local.min(axis=0)
        local_max = local.max(axis=0)
        minimum = world.min(axis=0)
        maximum = world.max(axis=0)
        dims = np.maximum(1, np.ceil((maximum - minimum) / spacing).astype(int))
        self._budget(int(np.prod(dims)))
        bvh = None
        if props.source_shape in {"MESH", "COLLECTION", "VOLUME", "INSTANCES"}:
            if len(triangles) == 0:
                raise RuntimeError(f"{obj.name}: closed mesh emission requires faces")
            bvh = self._source_volume_bvh(obj, props, history_key, world, triangles, require_closed=True)
        center = (local_min + local_max) / 2
        half_size = np.maximum((local_max - local_min) / 2, 1e-12)
        epsilon = max(1e-7, spacing * 1e-4)
        distance = float(np.linalg.norm(maximum - minimum) * 2 + spacing)
        positions = []
        lattice = np.indices(tuple(dims), dtype=np.float64).reshape(3, -1).T
        samples = minimum + (lattice + 0.5) * spacing
        start_phase, resolved_velocity = self._source_phase(history_key, props, motion_geometry)
        if sample_time is not None and np.any(resolved_velocity != 0):
            # A fixed inflow lattice aliases with occupancy filtering: entire
            # axial planes refill simultaneously and separate into falling discs.
            # Advect the lattice with uniform relative flow and distribute column
            # phases along its dominant axis, while keeping volume sampling.
            dims = dims + 2
            lattice = np.indices(tuple(dims), dtype=np.float64).reshape(3, -1).T - 1
            elapsed = max(0.0, min(self.dt, sample_time-self.time))
            phase = np.mod(start_phase + resolved_velocity * elapsed + spacing * .5, spacing) - spacing * .5
            samples = minimum + (lattice + .5) * spacing + phase
            axis = int(np.argmax(np.abs(resolved_velocity)))
            transverse = [i for i in range(3) if i != axis]
            column_phase = np.mod(lattice[:, transverse[0]] * 0.61803398875 +
                                  lattice[:, transverse[1]] * 0.41421356237, 1.0) - .5
            samples[:, axis] += column_phase * spacing
        inside_bounds = np.all((samples >= minimum) & (samples <= maximum), axis=1)
        samples = samples[inside_bounds]
        if bvh is None:
            # Evaluate analytic shapes in one array operation. Preserve the
            # original x/y/z sample ordering for deterministic emission.
            local_samples = samples @ inverse[:3, :3].T + inverse[:3, 3]
            norm = (local_samples - center) / half_size
            if props.source_shape == "BOX":
                inside = np.all(np.abs(norm) <= 1, axis=1)
            elif props.source_shape == "CYLINDER":
                inside = (np.einsum('ij,ij->i', norm[:, :2], norm[:, :2]) <= 1) & (np.abs(norm[:, 2]) <= 1)
            else:
                inside = np.einsum('ij,ij->i', norm, norm) <= 1
            positions = samples[inside]
        # Center samples avoid source-boundary overlaps.
        if bvh is not None:
            positions = [pos for pos in samples if _inside(bvh, pos, epsilon, distance)]
        positions = np.asarray(positions, dtype=np.float64).reshape((-1, 3))
        if len(positions) == 0:
            return positions, positions.copy()
        motion_world, motion_triangles, motion_matrix = motion_geometry[:3]
        previous = self.previous.get(history_key)
        unchanged = (previous is not None and np.array_equal(previous[0], motion_world)
                     and np.array_equal(previous[1], motion_matrix)
                     and np.array_equal(previous[2], motion_triangles))
        varying_attribute = (attr_velocity is not None and
                             not np.all(attr_velocity == attr_velocity[0]))
        needs_nearest = (mask is not None or varying_attribute or
                         (props.inherited_motion and previous is not None and not unchanged))
        if needs_nearest:
            tree = KDTree(len(world))
            for i, v in enumerate(world):
                tree.insert(Vector(v), i)
            tree.balance()
            nearest = np.asarray([tree.find(Vector(v))[1] for v in positions], dtype=np.int32)
        else:
            # Index zero gives the same absent/uniform attribute values and is
            # unused by absent masks and zero/no-history/unchanged motion.
            nearest = np.zeros(len(positions), dtype=np.int32)
        if mask is not None:
            active = mask[nearest] >= props.mask_threshold
            positions, nearest = positions[active], nearest[active]
        active = masks.active(props, positions, geometry, image_cache=self.image_cache, frame=self.mask_frame)
        positions, nearest = positions[active], nearest[active]
        velocities = self._velocities(obj, props, positions, nearest, motion_geometry, history_key, interior_samples=True)
        if props.normal_velocity and bvh is None and len(triangles):
            bvh = self._source_volume_bvh(obj, props, history_key, world, triangles, require_closed=False)
        if props.normal_velocity and bvh is not None:
            for i, v in enumerate(positions):
                hit, normal, index, dist = bvh.find_nearest(Vector(v))
                if normal is not None:
                    velocities[i] += np.asarray(normal) * props.normal_velocity
        return positions, velocities

    def _source_volume_bvh(self, obj, props, history_key, world, triangles, *, require_closed):
        key = (history_key, props.source_shape)
        cached = self.source_volume_cache.get(key)
        same_topology = cached is not None and np.array_equal(cached[1], triangles)
        closed = cached[2] if same_topology else None
        if require_closed and closed is None:
            edges = Counter(tuple(sorted((int(t[i]), int(t[(i + 1) % 3])))) for t in triangles for i in range(3))
            closed = all(count == 2 for count in edges.values())
        if require_closed and not closed:
            raise RuntimeError(f"{obj.name}: mesh emitter must be watertight; close boundary edges or use Box/Sphere")
        if same_topology and np.array_equal(cached[0], world):
            return cached[3]
        bvh = BVHTree.FromPolygons([Vector(v) for v in world], triangles.tolist(), all_triangles=True)
        # Own exact keys: callers can mutate their evaluated geometry arrays.
        self.source_volume_cache[key] = (world.copy(), triangles.copy(), closed, bvh)
        return bvh

    def _source_translation(self, history_key, geometry):
        if history_key not in self.source_motion_cache:
            previous = self.previous.get(history_key)
            world, triangles, matrix = geometry[:3]
            translation = None
            if (previous is not None and previous[0].shape == world.shape and
                    np.array_equal(previous[2], triangles) and
                    np.array_equal(previous[1][:3, :3], matrix[:3, :3])):
                delta = matrix[:3, 3]-previous[1][:3, 3]
                expected = previous[0] + delta
                # Local coordinates and matrices originate in Float32, but the
                # same linear products and world additions are evaluated here
                # in Float64. Allow only their bounded addition roundoff, not an
                # absolute tolerance that could hide representable deformation.
                rounding = np.finfo(np.float64).eps * 4 * (
                    np.abs(previous[0]) + np.abs(world) + np.abs(delta) +
                    np.abs(previous[1][:3, 3]) + np.abs(matrix[:3, 3]))
                if np.all(np.abs(expected-world) <= rounding):
                    translation = delta
            self.source_motion_cache[history_key] = translation
        return self.source_motion_cache[history_key]

    def _source_phase(self, history_key, props, geometry):
        # Different collection emitters may reference the same source geometry
        # while prescribing different flows. Motion history is shared geometry;
        # advection history belongs to both geometry and the owning emitter.
        phase_key = (history_key, props.id_data.as_pointer())
        if phase_key in self.source_phase_frames:
            return self.source_phase_frames[phase_key]
        world, triangles, matrix, mask, attr_velocity = geometry
        velocity = np.asarray(props.velocity, dtype=np.float64).copy()
        # Blender stores properties, attribute vectors, and evaluated transforms
        # in Float32. Bound unresolved cancellation from those inputs instead of
        # imposing an absolute velocity cutoff on genuinely slow flow.
        half_ulp = lambda value: np.abs(np.spacing(np.asarray(value, dtype=np.float32))).astype(np.float64) * .5
        uncertainty = half_ulp(props.velocity)
        if attr_velocity is not None and len(attr_velocity) and np.all(attr_velocity == attr_velocity[0]):
            # A proven uniform attribute advects a uniform lattice. Nonuniform
            # fields retain prescribed-flow phase; never substitute a mean.
            attribute = attr_velocity[0].astype(np.float64)
            linear = matrix[:3, :3]
            velocity += attribute @ linear.T
            uncertainty += (half_ulp(attribute) @ np.abs(linear).T +
                            np.abs(attribute) @ half_ulp(linear).T +
                            half_ulp(attribute) @ half_ulp(linear).T)
        translation = self._source_translation(history_key, geometry)
        start = velocity * self.time
        if translation is not None:
            start = self.source_phase.get(phase_key, start)
            velocity += (props.inherited_motion-1) * translation / self.dt
            old_matrix = self.previous[history_key][1]
            translation_uncertainty = (
                half_ulp(matrix[:3, 3]) + half_ulp(old_matrix[:3, 3])) / self.dt
            factor_uncertainty = half_ulp(props.inherited_motion)
            uncertainty += (abs(props.inherited_motion-1) * translation_uncertainty +
                            factor_uncertainty * np.abs(translation / self.dt) +
                            factor_uncertainty * translation_uncertainty)
        resolved = np.where(np.abs(velocity) <= uncertainty, 0.0, velocity)
        self.source_phase_frames[phase_key] = start, resolved
        return start, resolved

    def _record_source_phase(self, history_key, props, geometry, dt):
        start, velocity = self._source_phase(history_key, props, geometry)
        # Integrate every frame, including intervals with no emission ticks.
        # Absent/disabled emitters are pruned when updates replace history.
        self.source_phase_updates[(history_key, props.id_data.as_pointer())] = start + velocity * dt

    def _velocities(self, obj, props, positions, nearest, geometry, history_key=None, *, interior_samples=False):
        world, triangles, matrix, mask, attr_velocity = geometry
        velocities = np.tile(props.velocity, (len(positions), 1)).astype(np.float64)
        if attr_velocity is not None:
            velocities += attr_velocity[nearest] @ matrix[:3, :3].T
        previous = self.previous.get(history_key if history_key is not None else obj.as_pointer())
        if previous is not None and props.inherited_motion:
            old_world, old_matrix, old_triangles = previous
            if (np.array_equal(old_matrix, matrix) and np.array_equal(old_world, world)
                    and np.array_equal(old_triangles, triangles)):
                # An unchanged source has exactly zero inherited velocity, even
                # when inverse/multiply would reconstruct small numerical noise.
                return velocities
            if interior_samples:
                # Interior samples do not sit at their nearest mesh vertex.
                # Preserve exact rigid material motion at each sample, adding
                # only the existing nearest-vertex approximation for deformation.
                local = (positions-matrix[:3, 3]) @ np.linalg.inv(matrix[:3, :3]).T
                old_positions = local @ old_matrix[:3, :3].T+old_matrix[:3, 3]
                motion = (positions-old_positions) / self.dt
                if len(old_world) == len(world) and np.array_equal(old_triangles, triangles):
                    local_vertex = (world[nearest]-matrix[:3, 3]) @ np.linalg.inv(matrix[:3, :3]).T
                    rigid_old_vertex = local_vertex @ old_matrix[:3, :3].T+old_matrix[:3, 3]
                    motion += (rigid_old_vertex-old_world[nearest]) / self.dt
            elif len(old_world) == len(world) and np.array_equal(old_triangles, triangles):
                motion = (world[nearest] - old_world[nearest]) / self.dt
            else:
                local = (positions - matrix[:3, 3]) @ np.linalg.inv(matrix[:3, :3]).T
                old_positions = local @ old_matrix[:3, :3].T + old_matrix[:3, 3]
                motion = (positions - old_positions) / self.dt
            velocities += motion * props.inherited_motion
        return velocities

    def _boundary(self, obj, props, geometry, spacing, history_key=None):
        world, triangles, matrix, mask, attr_velocity = geometry
        history_key = history_key if history_key is not None else obj.as_pointer()
        cached = self.boundary_cache.get(history_key)
        if cached is not None and cached[2] == spacing and np.array_equal(cached[0], world) and np.array_equal(cached[1], triangles):
            still = self.boundary_still.get(history_key)
            if still is None or still[0] is not cached[3]:
                still = self.boundary_still[history_key] = (cached[3], np.zeros_like(cached[3]))
            self.boundary_unmoved.add(history_key)
            return cached[3], still[1]
        if not len(triangles):
            raise RuntimeError(f"{obj.name}: collider requires evaluated mesh faces")
        previous = self.previous.get(history_key)
        old_world = previous[0] if previous is not None and len(previous[0]) == len(world) and np.array_equal(previous[2], triangles) else None
        # A mirrored object (negative scale) reverses the winding of its world-space faces.
        facing = -1.0 if np.linalg.det(np.asarray(matrix)[:3, :3]) < 0 else 1.0
        corners = world[triangles]
        edges = np.linalg.norm(corners - np.roll(corners, -1, axis=1), axis=2)
        lattice = self.boundary_lattice.get(history_key)
        if (lattice is not None and lattice[1] == spacing and lattice[4] == facing and np.array_equal(lattice[0], triangles)
                and (old_world is not None or previous is None)
                and np.allclose(edges, lattice[2], rtol=1e-5, atol=spacing * 1e-5)):
            # The mesh has moved without changing shape (an animated rigid
            # object, the usual moving collider): its samples keep their place
            # on the surface, so only their positions are recomputed instead
            # of rastering every triangle again.
            owner, weights = lattice[3], lattice[5]
            result = np.einsum('nk,nkj->nj', weights, corners[owner])
            velocity = ((result - np.einsum('nk,nkj->nj', weights, old_world[triangles][owner])) / self.dt
                        if old_world is not None else np.zeros_like(result))
            normal = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
            normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-300)
            self.boundary_normals[history_key] = _merged_normals(facing * normal[lattice[6]], lattice[7], len(owner))
            self.boundary_cache[history_key] = (world, triangles, spacing, result)
            return result, velocity
        # Every triangle gets a lattice of samples `spacing` apart in its own
        # plane (first edge along x) plus samples along its three edges. All
        # triangles are rastered together: one Python loop per triangle took
        # seconds for a mesh of tens of thousands of faces.
        limit = limits(self.domain.fumaris_liquid)[1]
        a, ab, ac = corners[:, 0], corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0]
        edge_length = np.linalg.norm(ab, axis=1)
        normal = np.cross(ab, ac)
        normal_length = np.linalg.norm(normal, axis=1)
        valid = (edge_length >= 1e-10) & (normal_length >= 1e-12)
        safe_edge, safe_normal = np.where(valid, edge_length, 1.0), np.where(valid, normal_length, 1.0)
        tangent = ab / safe_edge[:, None]
        bitangent = np.cross(normal / safe_normal[:, None], tangent)
        cx, cy = np.einsum('ij,ij->i', ac, tangent), np.einsum('ij,ij->i', ac, bitangent)
        cy = np.where(valid, cy, 1.0)
        first_x = np.minimum(0.0, cx) + spacing * 0.5
        columns = np.where(valid, np.maximum(np.ceil((np.maximum(edge_length, cx) - first_x) / spacing), 0), 0).astype(np.int64)
        rows = np.where(valid, np.maximum(np.ceil((cy - spacing * 0.5) / spacing), 0), 0).astype(np.int64)
        cells = columns * rows
        if len(cells) and cells.max() > 2000000:
            raise RuntimeError(f"{obj.name}: collider sampling exceeds its candidate budget; raise radius")
        if int(cells.sum()) > max(8 * limit, 16000000):
            raise RuntimeError(_over(self.domain.fumaris_liquid, f"{obj.name}: collider sampling", "simplify the mesh or increase the particle radius"))
        inside = np.repeat(np.arange(len(triangles)), cells)
        within = np.arange(len(inside)) - np.repeat(np.cumsum(cells) - cells, cells)
        x = first_x[inside] + (within % np.maximum(columns, 1)[inside]) * spacing
        y = spacing * 0.5 + (within // np.maximum(columns, 1)[inside]) * spacing
        wc = y / cy[inside]
        wb = (x - wc * cx[inside]) / safe_edge[inside]
        lattice_weights = np.column_stack((1 - wb - wc, wb, wc))
        keep = np.all(lattice_weights >= 0, axis=1)
        owner_parts, weight_parts, order_parts = [inside[keep]], [lattice_weights[keep]], [inside[keep] * 4]
        for part, (first, second) in enumerate(((0, 1), (1, 2), (2, 0)), start=1):
            length = np.linalg.norm(corners[:, first] - corners[:, second], axis=1)
            segments = np.where(valid, np.maximum(1, np.ceil(length / spacing)), 0).astype(np.int64)
            if len(segments) and segments.max() > limit:
                raise RuntimeError(_over(self.domain.fumaris_liquid, f"{obj.name}: collider edge sampling", "increase the particle radius"))
            count = np.where(valid, segments + 1, 0)
            along = np.repeat(np.arange(len(triangles)), count)
            step = np.arange(len(along)) - np.repeat(np.cumsum(count) - count, count)
            fraction = np.where(step == segments[along], 1.0, step * (1.0 / np.maximum(segments, 1)[along]))
            edge_weights = np.zeros((len(along), 3))
            edge_weights[:, first], edge_weights[:, second] = 1 - fraction, fraction
            owner_parts.append(along); weight_parts.append(edge_weights); order_parts.append(along * 4 + part)
        # Triangle by triangle: its lattice, then its edges, as one loop over triangles produced them.
        order = np.argsort(np.concatenate(order_parts), kind='stable')
        owner, weights = np.concatenate(owner_parts)[order], np.concatenate(weight_parts)[order]
        if len(weights) > limit:
            raise RuntimeError(_over(self.domain.fumaris_liquid, f"{obj.name}: collider sampling", "simplify the mesh or increase the particle radius"))
        points = np.einsum('nk,nkj->nj', weights, corners[owner]) if len(owner) else np.empty((0, 3))
        if old_world is not None:
            velocities = (points - np.einsum('nk,nkj->nj', weights, old_world[triangles][owner])) / self.dt
        elif previous is not None and not self.boundary_motion_from_velocity and len(points):
            # Retain the old transform fallback only for legacy timelines.
            local = (points - matrix[:3, 3]) @ np.linalg.inv(matrix[:3, :3]).T
            velocities = (points - (local @ previous[1][:3, :3].T + previous[1][:3, 3])) / self.dt
        else:
            # Newly introduced or incompatible topology has no proven prior
            # surface correspondence. Place it at the current pose.
            velocities = np.zeros_like(points)
        normals = (facing * normal / safe_normal[:, None])[owner]
        # Shared-edge samples are intentionally deduplicated.
        _, unique, group = np.unique(np.round(points / (spacing * 1e-5)).astype(np.int64), axis=0, return_index=True, return_inverse=True)
        group = np.asarray(group).reshape(-1)
        result = points[unique]
        self.boundary_normals[history_key] = _merged_normals(normals, group, len(unique))
        self.boundary_cache[history_key] = (world, triangles, spacing, result)
        if len(unique):
            self.boundary_lattice[history_key] = (triangles, spacing, edges, owner[unique], facing, weights[unique], owner, group)
        return result, velocities[unique]

    def _points(self, obj, props, depsgraph):
        evaluated = obj.evaluated_get(depsgraph)
        if props.source_shape == "PARTICLES":
            points, velocities = [], []
            for system in evaluated.particle_systems:
                for particle in system.particles:
                    if particle.alive_state == "ALIVE":
                        points.append(tuple(particle.location))
                        velocities.append(np.asarray(particle.velocity) * props.inherited_motion + np.asarray(props.velocity))
            positions = np.asarray(points).reshape((-1, 3))
            velocities = np.asarray(velocities).reshape((-1, 3))
            active = masks.active(props, positions, matrix=np.asarray(evaluated.matrix_world), image_cache=self.image_cache, frame=self.mask_frame)
            return positions[active], velocities[active], None
        data = evaluated.data
        position = _attribute(data, "position", True)
        if position is not None and len(position):
            matrix = np.asarray(evaluated.matrix_world)
            world = position @ matrix[:3, :3].T + matrix[:3, 3]
            triangles = np.empty((0, 3), dtype=np.int32)
            geometry = (world, triangles, matrix, _attribute(data, props.mask_attribute), _attribute(data, props.velocity_attribute, True))
        else:
            try:
                geometry = self._geometry(obj, depsgraph, props)
            except RuntimeError:
                instances = self._instances(obj, props, depsgraph)
                if not instances:
                    raise RuntimeError(f"{obj.name}: no evaluated points, vertices or instance origins")
                world = np.asarray([item[1][2][:3, 3] for item in instances])
                geometry = (world, np.empty((0, 3), dtype=np.int32), np.asarray(evaluated.matrix_world), None, None)
            world, triangles, matrix, mask, attr_velocity = geometry
        mask = geometry[3]
        nearest = np.arange(len(world))
        if mask is not None:
            nearest = nearest[mask >= props.mask_threshold]
        positions = world[nearest]
        active = masks.active(props, positions, geometry, image_cache=self.image_cache, frame=self.mask_frame)
        positions, nearest = positions[active], nearest[active]
        return positions, self._velocities(obj, props, positions, nearest, geometry), geometry

    def _strays(self, p, clouds):
        """Outflow slabs that remove liquid which has left the scene for good.

        The box grows around the region and every collider and emitter seen so
        far; liquid beyond it by a margin, below or to the sides, has nothing
        left to land on. The side gravity points away from stays open, since
        liquid thrown that way comes back.
        """
        low, high = np.array(p.bounds_min, dtype=np.float64), np.array(p.bounds_max, dtype=np.float64)
        for cloud in clouds:
            if len(cloud):
                low, high = np.minimum(low, cloud.min(axis=0)), np.maximum(high, cloud.max(axis=0))
        if self.reach is not None:
            low, high = np.minimum(low, self.reach[0]), np.maximum(high, self.reach[1])
        self.reach = low, high
        margin = max(1.0, 0.25 * float((high - low).max()))
        gravity = np.array(p.gravity, dtype=np.float64)
        down = int(np.abs(gravity).argmax()) if np.any(gravity) else None
        slabs = []
        for axis in range(3):
            for side in (0, 1):
                if axis == down and (gravity[axis] < 0) == bool(side):
                    continue
                a, b = np.full(3, -STRAY_REACH), np.full(3, STRAY_REACH)
                if side:
                    a[axis] = high[axis] + margin
                else:
                    b[axis] = low[axis] - margin
                slabs.append([a.tolist(), b.tolist()])
        return slabs

    def frame(self, depsgraph, dt, frame):
        self.dt = max(dt, 1e-8)
        self.mask_frame = frame
        self.birth_solid_cache.clear()
        self.birth_motion_cache.clear()
        self.source_volume_active.clear()
        self.source_motion_cache.clear()
        self.source_phase_updates.clear()
        self.source_phase_frames.clear()
        self.boundary_unmoved.clear()
        self.image_cache = {}
        p = self.domain.fumaris_liquid
        spacing = p.particle_radius * 2
        boundary_parts = []
        emitted, emitted_velocity, boundary, boundary_velocity = [], [], [], []
        boundary_normal = []
        emission_batches = []
        emitted_count = 0
        excluded_count = 0
        outflows, forces, current = [], [], {}
        objects = list(participants(self.scene, self.domain))
        collider_geometry, solids, solid_keys = {}, [], set()
        # Evaluate solids before emitters regardless of collection/object order.
        # Geometry is reused below, so this adds no second Blender evaluation.
        for obj in objects:
            props = obj.fumaris_liquid
            if props.role != 'COLLIDER' or not props.enabled:
                continue
            try:
                geometries = [(obj.as_pointer(), self._geometry(obj, depsgraph, props))]
            except RuntimeError:
                geometries = self._instances(obj, props, depsgraph)
                if not geometries:
                    raise
            collider_geometry[obj.as_pointer()] = geometries
            for key, geometry in geometries:
                solid = self._solid(key, geometry)
                solid_keys.add(key)
                if solid is not None:
                    solids.append(solid)
        self.solid_cache = {key:value for key,value in self.solid_cache.items() if key in solid_keys}
        for obj in objects:
            props = obj.fumaris_liquid
            if not props.enabled:
                continue
            if props.role == "EMITTER" and props.emission_mode == "INITIAL" and obj.as_pointer() in self.initialized:
                continue
            evaluated = obj.evaluated_get(depsgraph)
            if props.role == "EFFECTOR":
                # Centre at the object, axis its local Z; accelerations in the scene's length unit.
                scale = max(1e-8, self.scene.unit_settings.scale_length)
                axis = (evaluated.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()
                forces.append({"kind": props.effector_kind, "center": list(evaluated.matrix_world.translation),
                               "vector": list(props.force_vector) if props.effector_kind == "FORCE" else list(axis),
                               "strength": props.force_strength / scale, "radius": props.force_radius})
                if len(forces) > 31:
                    raise RuntimeError("A simulation supports at most 31 effectors")
                continue
            if props.role == "OUTFLOW":
                corners = np.asarray([tuple(evaluated.matrix_world @ Vector(v)) for v in evaluated.bound_box])
                outflows.append([corners.min(axis=0).tolist(), corners.max(axis=0).tolist()])
                continue
            source_objects = list(props.source_collection.all_objects) if props.role == "EMITTER" and props.source_shape == "COLLECTION" and props.source_collection else [obj]
            if props.role == "EMITTER" and props.source_shape == "COLLECTION" and props.source_collection is None:
                raise RuntimeError(f"{obj.name}: assign a source collection")
            emit = props.role == "EMITTER" and obj.as_pointer() not in self.initialized
            repetitions = 1 if emit else 0
            emission_times = [0.0] if emit else []
            if props.role == "EMITTER" and props.emission_mode == "CONTINUOUS":
                # Preserve the actual tick times. Sending several identical
                # batches before a single step makes the overlap filter reject
                # all but the first, so frequency stops increasing real flow.
                pointer = obj.as_pointer()
                next_tick = max(self.time, self.next_emission.get(pointer, self.time))
                frequency = props.emission_frequency
                repetitions = max(0, math.ceil((self.time + dt - next_tick - 1e-10) * frequency))
                emission_times = [max(0.0, next_tick + tick / frequency - self.time)
                                  for tick in range(repetitions)]
                self.next_emission[pointer] = next_tick + repetitions / frequency
            source_emitted = False
            for source in sorted(source_objects, key=lambda value: value.name):
                if source.type not in {"MESH", "CURVE", "SURFACE", "FONT", "META", "POINTCLOUD", "VOLUME"}:
                    if props.source_shape == "COLLECTION":
                        continue
                    raise RuntimeError(f"{source.name}: this source requires mesh, points, instances or volume geometry")
                if props.role == "EMITTER" and props.source_shape in {"POINTS", "PARTICLES"}:
                    positions, velocities, geometry = self._points(source, props, depsgraph)
                    items = [(source.as_pointer(), geometry, positions, velocities)]
                else:
                    if props.role == 'COLLIDER':
                        geometries = collider_geometry[obj.as_pointer()]
                    elif props.source_shape == "VOLUME" and props.role == "EMITTER":
                        geometries = [(source.as_pointer(), volume_export.geometry(self.scene, source, props, self._geometry))]
                    elif props.source_shape == "INSTANCES" and props.role == "EMITTER":
                        geometries = self._instances(source, props, depsgraph)
                        if not geometries:
                            raise RuntimeError(f"{source.name}: no evaluated mesh instances; use Geometry Nodes instance geometry")
                    else:
                        try:
                            geometries = [(source.as_pointer(), self._geometry(source, depsgraph, props))]
                        except RuntimeError:
                            geometries = self._instances(source, props, depsgraph)
                            if not geometries:
                                raise
                    items = []
                    for key, geometry in geometries:
                        if props.role == "EMITTER":
                            self.source_volume_active.add((key, props.source_shape))
                        if props.role == "COLLIDER":
                            positions, velocities = self._boundary(source, props, geometry, p.particle_radius, key)
                        elif repetitions:
                            if props.emission_mode == "CONTINUOUS":
                                self._record_source_phase(key, props, geometry, dt)
                            sample_time = self.time + emission_times[0] if props.emission_mode == "CONTINUOUS" else None
                            positions, velocities = self._volume(source, props, geometry, spacing, key, sample_time)
                        else:
                            if props.role == "EMITTER" and props.emission_mode == "CONTINUOUS":
                                self._record_source_phase(key, props, geometry, dt)
                            positions = velocities = np.empty((0, 3))
                        items.append((key, geometry, positions, velocities))
                for key, geometry, positions, velocities in items:
                    if geometry is not None:
                        current[key] = (geometry[0], geometry[2], geometry[1])
                    if props.role == "COLLIDER":
                        boundary_parts.append((key, id(positions), key in self.boundary_unmoved, len(positions)))
                        boundary.append(positions)
                        boundary_velocity.append(velocities)
                        # The side liquid may not enter. Samples without a mesh face behind them send none.
                        facing = self.boundary_normals.get(key)
                        boundary_normal.append(facing if facing is not None and len(facing) == len(positions)
                                               else np.zeros_like(positions))
                    elif repetitions:
                        source_emitted |= bool(len(positions))
                        for emission_time in emission_times:
                            if props.emission_mode == "CONTINUOUS" and emission_time != emission_times[0] and props.source_shape not in {"POINTS", "PARTICLES"}:
                                positions, velocities = self._volume(source, props, geometry, spacing, key,
                                                                     self.time + emission_time)
                            clipped, clipped_velocity, excluded = self._clip_birth_solids(
                                positions, velocities, collider_geometry, solids, p.particle_radius, emission_time)
                            excluded_count += excluded
                            emitted.append(clipped)
                            emitted_velocity.append(clipped_velocity)
                            if len(clipped):
                                emission_batches.append({"offset": emitted_count, "count": len(clipped),
                                                         "time": emission_time})
                                emitted_count += len(clipped)
            if props.role == "EMITTER" and repetitions and source_emitted:
                self.initialized.add(obj.as_pointer())
        if p.domain_walls:
            key = "domain_walls"
            geometry = domain_walls(p)
            positions, velocities = self._boundary(self.domain, None, geometry, p.particle_radius, key)
            current[key] = (geometry[0], geometry[2], geometry[1])
            boundary_parts.append((key, id(positions), key in self.boundary_unmoved, len(positions)))
            boundary.append(positions)
            boundary_velocity.append(velocities)
            boundary_normal.append(self.boundary_normals[key])
        self.source_volume_cache = {key: value for key, value in self.source_volume_cache.items()
                                    if key in self.source_volume_active}
        self.source_phase = self.source_phase_updates.copy()
        self.previous = current
        self.time += dt
        concat = lambda values: np.concatenate(values, axis=0) if values else np.empty((0, 3))
        # Every collider is where it was and the last frame was built from the
        # same sample arrays, all at rest: the worker already holds exactly
        # these boundaries. Joining, converting and comparing a few hundred
        # thousand samples to find that out again was most of a frame's export
        # in a static scene.
        boundary_parts = tuple(boundary_parts)
        unchanged = (self.last_boundary_arrays is not None and boundary_parts == self.last_boundary_parts
                     and all(part[2] for part in boundary_parts))
        # Some colliders moved and the rest are as the worker has them: send
        # the moved ones only, as patches into the arrays of the last frame.
        last_parts, patches = self.last_boundary_parts, None
        if (not unchanged and self.last_boundary_arrays is not None and last_parts is not None
                and len(last_parts) == len(boundary_parts)
                and all(a[0] == b[0] and a[3] == b[3] for a, b in zip(boundary_parts, last_parts))
                and len(self.last_boundary_arrays[2]) == sum(part[3] for part in boundary_parts)):
            moved = [index for index, (part, last) in enumerate(zip(boundary_parts, last_parts))
                     if part[3] and not (part[2] and last[2] and part[1] == last[1])]
            if moved and sum(boundary_parts[index][3] for index in moved) * 2 < len(self.last_boundary_arrays[0]):
                offsets = np.cumsum([0] + [part[3] for part in boundary_parts])
                patches = [[int(offsets[index]), int(boundary_parts[index][3])] for index in moved]
                sent = [np.ascontiguousarray(np.concatenate([parts[index] for index in moved], axis=0), dtype='<f4')
                        for parts in (boundary, boundary_velocity, boundary_normal)]
                position = 0
                for offset, count in patches:
                    for kept, part in zip(self.last_boundary_arrays, sent):
                        kept[offset:offset + count] = part[position:position + count]
                    position += count
        self.last_boundary_parts = boundary_parts
        arrays = {"positions": concat(emitted), "velocities": concat(emitted_velocity)}
        if patches is not None:
            arrays.update({"boundary_positions": sent[0], "boundary_velocities": sent[1], "boundary_normals": sent[2]})
        elif not unchanged:
            arrays.update({"boundary_positions": concat(boundary), "boundary_velocities": concat(boundary_velocity),
                           "boundary_normals": concat(boundary_normal)})
        self._budget(len(arrays["positions"]))
        if not unchanged and patches is None and len(arrays["boundary_positions"]) > limits(p)[1]:
            raise RuntimeError(_over(p, "Combined collider sampling", "simplify geometry or increase the particle radius"))
        # Unchanged colliders are already inside the box _strays keeps.
        strays = self._strays(p, (arrays["positions"],) if unchanged else (arrays["positions"], arrays["boundary_positions"])) if p.remove_strays else []
        if len(outflows) + len(strays) > 32:
            raise RuntimeError(f"A simulation supports at most {32 - len(strays)} outflows")
        outflows = outflows + strays
        if unchanged:
            reuse = True
        elif patches is not None:
            reuse = False
        else:
            boundary_arrays = tuple(np.ascontiguousarray(arrays[name], dtype='<f4')
                                    for name in ('boundary_positions', 'boundary_velocities', 'boundary_normals'))
            reuse = self.last_boundary_arrays is not None and all(
                np.array_equal(a, b) for a, b in zip(boundary_arrays, self.last_boundary_arrays))
            if self.boundary_motion_from_velocity and np.any(boundary_arrays[1] != 0):
                # Moving endpoints must be supplied for every emission interval.
                reuse = False
            self.last_boundary_arrays = boundary_arrays
            if reuse:
                del arrays['boundary_positions'], arrays['boundary_velocities'], arrays['boundary_normals']
        blocks, payload = pack_blocks(arrays)
        fps = self.scene.render.fps / max(self.scene.render.fps_base, 1e-8)
        return {"frame": int(frame), "dt": float(dt), "blocks": blocks,
                # Renderers displace by velocity / fps; a time-scaled simulation
                # moves by velocity * dt, so stored motion vectors are rescaled.
                "motion_scale": float(dt * fps),
                "emission_batches": emission_batches,
                "emission_clipped": excluded_count,
                "reuse_boundaries": reuse,
                **({"boundary_patches": patches} if patches is not None else {}),
                "boundary_motion_from_velocity": self.boundary_motion_from_velocity,
                "outflows": outflows, "forces": forces}, payload
