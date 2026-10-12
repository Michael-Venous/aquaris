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


def _sphere(name, collection, location, radius):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    try:
        bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=radius)
        bm.to_mesh(mesh)
    finally:
        bm.free()
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.location = location
    return obj


# Presets that run in real time on the grid solver with a surface shown.
REALTIME = {"SPLASH", "REALTIME_DAM_BREAK", "REALTIME_POUR"}


def _realtime(p):
    """Grid solver settings measured to hold about 20-28 frames a second with a
    surface on an RTX 4060 Ti: 15,000-26,000 particles, surface built on the GPU."""
    p.solver = "FLIP"
    p.particle_radius = 0.028
    p.display = "SURFACE"
    p.mesh_quality = "GPU_SMOOTH"
    p.mesh_cell_relative = True
    p.mesh_cell_factor = 1.0
    p.surface_smoothing = 1.0
    p.jet_limit = 2.0
    p.substeps = 1
    # The grid solver makes no whitewater; leave the switch off so a later
    # change of solver starts from what was seen.
    p.whitewater = False


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
    if preset in REALTIME:
        _realtime(p)
    if preset == "SPLASH":
        # A ball dropped into a pool: the crown a solid impact throws up is
        # what the grid solver does and the particle solver cannot.
        emitter = _box("Initial Pool", emitters, origin + Vector((0, 0, 0.26)), (3.94, 2.04, 0.5))
        emitter.fumaris_liquid.emission_mode = "INITIAL"
        ball = _sphere("Falling Ball", colliders, origin + Vector((0, 0, 2.0)), 0.32)
        ball.fumaris_liquid.role = "COLLIDER"
        start = scene.frame_current
        for frame, height in ((start, 2.0), (start + 8, 2.0), (start + 14, 0.36)):
            ball.location = origin + Vector((0, 0, height))
            ball.keyframe_insert("location", frame=frame)
        # Constant speed down to the stop, about 6.5 m/s at 24 frames a second.
        for layer in ball.animation_data.action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        for key in curve.keyframe_points:
                            key.interpolation = "LINEAR"
        ball.location = origin + Vector((0, 0, 2.0))
    elif preset == "REALTIME_DAM_BREAK":
        # The block fills the basin's end from wall to wall, as in a dam-break
        # experiment, and runs into two pillars.
        emitter = _box("Initial Liquid", emitters, origin + Vector((-1.28, 0, 0.56)), (1.4, 2.04, 1.1))
        emitter.fumaris_liquid.emission_mode = "INITIAL"
        for name, location, size in (("Pillar Near", (0.35, -0.42, 0.5), (0.3, 0.3, 1.0)),
                                     ("Pillar Far", (1.0, 0.38, 0.4), (0.36, 0.36, 0.8))):
            pillar = _box(name, colliders, origin + Vector(location), size)
            pillar.fumaris_liquid.role = "COLLIDER"
    elif preset == "DAM_BREAK":
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
            # Thick liquids read as one glossy material: keep droplets whole
            # and remove particle-scale dimples from the surface.
            p.surface_smoothing = 0.9
            p.droplet_scale = 1.0
            p.surface_tension = 0.2
            p.substeps = 4
    emitter.fumaris_liquid.role = "EMITTER"
    emitter.fumaris_liquid.source_shape = "BOX" if preset in {"DAM_BREAK", "REALTIME_DAM_BREAK", "SPLASH"} else "CYLINDER"
    emitter.display_type = "WIRE"
    emitter.hide_render = True
    display.ensure_outputs(scene, domain)
    for obj in context.selected_objects:
        obj.select_set(False)
    domain.select_set(True)
    context.view_layer.objects.active = domain
    return domain
