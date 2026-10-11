"""Create isolated, undoable demonstration scenes without replacing existing data."""

import bpy
import bmesh
from mathutils import Vector

from . import display


def _collection(name, parent):
    value = bpy.data.collections.new(name)
    parent.children.link(value)
    return value


def _box(name, collection, location, size):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        bmesh.ops.create_cube(bm, size=1)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.location = location
    obj.scale = size
    return obj


def _cylinder(name, collection, location, size):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=48,
                             radius1=0.5, radius2=0.5, depth=1)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.location = location
    obj.scale = size
    return obj


def create(context, preset="DAM_BREAK"):
    scene = context.scene
    origin = scene.cursor.location.copy()
    root = _collection("Aquaris " + preset.title().replace("_", " "), scene.collection)
    emitters = _collection("Liquid Emitters", root)
    colliders = _collection("Liquid Colliders", root)
    effectors = _collection("Liquid Effectors", root)
    outflows = _collection("Liquid Outflows", root)
    domain = bpy.data.objects.new("Aquaris Domain", None)
    root.objects.link(domain)
    domain.location = origin
    domain.empty_display_type = "PLAIN_AXES"
    domain.empty_display_size = 2
    p = domain.fumaris_liquid
    p.role = "DOMAIN"
    p.emitter_collection = emitters
    p.collider_collection = colliders
    p.effector_collection = effectors
    p.outflow_collection = outflows
    p.bounds_min = origin + Vector((-2.6, -1.6, -0.4))
    p.bounds_max = origin + Vector((2.6, 1.6, 3.4))
    p.bake_start = scene.frame_current
    p.bake_end = scene.frame_current + 119
    # At the old .065 radius the default nozzle was only three particles wide,
    # and the basin became a one-particle-thick sheet. A useful artistic preview
    # needs enough samples across both the jet and the pool depth.
    p.particle_radius = 0.035
    p.mesh_cell_size = 0.035
    p.iterations = 6
    p.substeps = 3
    p.viscosity = 0.015
    p.surface_tension = 0.08
    p.whitewater_generation = 0.5
    display.ensure_identity(domain)
    # Five actual solids create a basin; the domain allocation is not a wall.
    walls = [
        ("Basin Floor", (0, 0, -0.15), (4.5, 2.7, 0.3)),
        ("Basin Left", (-2.15, 0, 1), (0.3, 2.7, 2.3)),
        ("Basin Right", (2.15, 0, 1), (0.3, 2.7, 2.3)),
        ("Basin Front", (0, -1.2, 1), (4.5, 0.3, 2.3)),
        ("Basin Back", (0, 1.2, 1), (4.5, 0.3, 2.3)),
    ]
    for name, location, size in walls:
        obj = _box(name, colliders, origin + Vector(location), size)
        obj.fumaris_liquid.role = "COLLIDER"
        obj.display_type = "WIRE"
    if preset == "DAM_BREAK":
        emitter = _box("Initial Liquid", emitters, origin + Vector((-1.15, 0, 0.85)), (1.2, 1.8, 1.5))
        emitter.fumaris_liquid.emission_mode = "INITIAL"
    else:
        # A shallow existing pool makes splashes and recovery visible from the
        # first second without spending the user's iteration time filling a tank.
        pool = _box("Initial Pool", emitters, origin + Vector((0, 0, 0.30)), (3.7, 1.9, 0.35))
        pool.fumaris_liquid.role = "EMITTER"
        pool.fumaris_liquid.source_shape = "BOX"
        pool.fumaris_liquid.emission_mode = "INITIAL"
        pool.display_type = "WIRE"
        pool.hide_render = True
        emitter = _cylinder("Liquid Inflow", emitters, origin + Vector((0, 0, 2.6)), (0.55, 0.55, 0.30))
        emitter.fumaris_liquid.emission_mode = "CONTINUOUS"
        emitter.fumaris_liquid.velocity = (0, 0, -2)
        emitter.fumaris_liquid.emission_frequency = 48
        if preset == "VISCOUS_POUR":
            p.viscosity = 0.02
            p.kinematic_viscosity = 0.005
            p.surface_tension = 0.2
            p.substeps = 4
    emitter.fumaris_liquid.role = "EMITTER"
    emitter.fumaris_liquid.source_shape = "BOX" if preset == "DAM_BREAK" else "CYLINDER"
    emitter.display_type = "WIRE"
    emitter.hide_render = True
    display.ensure_outputs(scene, domain)
    for obj in context.selected_objects:
        obj.select_set(False)
    domain.select_set(True)
    context.view_layer.objects.active = domain
    return domain
