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


def _tray(name, collection, origin):
    """The Wave Tank's open tray (one mesh, faces towards the liquid): a flat floor, a beach rising towards -Y,
    and walls round both. Open at the top, so it is given a Margin and holds liquid from either side."""
    w, front, back, beach, rise, height = 1.2, -1.4, 2.0, -4.2, 0.45, 0.7
    low = [(-w, front, 0), (w, front, 0), (w, back, 0), (-w, back, 0), (-w, beach, rise), (w, beach, rise)]
    points = [Vector(v) for v in low] + [Vector(v) + Vector((0, 0, height)) for v in low]
    # each face with the direction its front must face (into the tray)
    faces = [((0, 1, 2, 3), (0, 0, 1)), ((4, 5, 1, 0), (0, 0, 1)),                 # floor, beach
             ((0, 3, 9, 6), (1, 0, 0)), ((4, 0, 6, 10), (1, 0, 0)),                # left wall
             ((1, 2, 8, 7), (-1, 0, 0)), ((5, 1, 7, 11), (-1, 0, 0)),              # right wall
             ((2, 3, 9, 8), (0, -1, 0)), ((4, 5, 11, 10), (0, 1, 0))]              # back wall, wall behind the beach
    polygons = []
    for indices, inward in faces:
        a, b, c = (points[i] for i in indices[:3])
        if (b - a).cross(c - a).dot(Vector(inward)) < 0:
            indices = tuple(reversed(indices))
        polygons.append(indices)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in points], [], polygons)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.location = origin
    return obj


# Presets that run in real time with a surface shown.
REALTIME = {"SPLASH", "REALTIME_DAM_BREAK", "REALTIME_POUR", "WAVE_TANK"}
# The same scenes at a finer resolution with the Smooth mesh, meant for baking.
DETAILED = {"DAM_BREAK": "REALTIME_DAM_BREAK", "POUR": "REALTIME_POUR"}


def _realtime(p):
    """Settings measured to hold about 20-28 frames a second with a surface on
    an RTX 4060 Ti: 15,000-26,000 particles, surface built on the GPU."""
    p.particle_radius = 0.028
    p.display = "PREVIEW"  # the fastest view; Surface or a bake for renderable geometry
    p.mesh_quality = "GPU_SMOOTH"
    p.mesh_cell_relative = True
    p.mesh_cell_factor = 1.0
    p.surface_smoothing = 1.0
    p.step_size = 3.0
    p.collider_step = 1.8
    p.substeps = 1
    # Whitewater shows where it is generated; only the Splash generates it (about 3 ms a frame there).
    p.whitewater = True


def _detailed(p):
    """The real-time settings (GPU preview, large steps) at 2.7 times the particles
    (radius 0.02): about 48,000 particles in the dam break. Use Step Size 1
    and Surface Cell 1 for an accurate, detailed bake."""
    _realtime(p)
    p.particle_radius = 0.02
    p.mesh_cell_size = 0.02


def _collapse_outliners():
    """Collapse every collection in the View Layer outliners, leaving the Scene Collection open.

    Blender opens a new collection the first time an outliner draws it, and its outliner has no API for one
    element: everything is opened (an object's contents start closed, so the first toggle opens), closed, and
    the top level opened again. Runs from a timer, after the outliners have drawn the new collections."""
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != "OUTLINER" or area.spaces.active.display_mode != "VIEW_LAYER":
                    continue
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                if region is None:
                    continue
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.outliner.expanded_toggle()
                    bpy.ops.outliner.expanded_toggle()
                    bpy.ops.outliner.show_one_level(open=True)
                area.tag_redraw()
    except (ReferenceError, RuntimeError):
        pass  # a closed window or an outliner in another state: cosmetic only
    return None


def collapse_outliners_soon():
    if not bpy.app.background:
        bpy.app.timers.register(_collapse_outliners, first_interval=0.1)


def create(context, preset="DAM_BREAK"):
    if preset == "DROPLETS":
        return _droplets(context)
    if preset not in REALTIME and preset not in DETAILED:
        raise ValueError(f"Unknown Aquaris setup: {preset}")
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
    if preset == "WAVE_TANK":
        p.bounds_min = origin + Vector((-1.6, -4.6, -0.4))
        p.bounds_max = origin + Vector((1.6, 2.4, 1.6))
    else:
        p.bounds_min = origin + Vector((-2.6, -1.6, -0.4))
        p.bounds_max = origin + Vector((2.6, 1.6, 3.4))
    # A preset is a set length from the current frame, not the timeline (as Fumaris' presets with a duration).
    p.use_scene_frame_range = False
    p.bake_start = scene.frame_current
    p.bake_end = scene.frame_current + 119
    p.mesh_cell_size = 0.035
    display.ensure_identity(domain)
    # Initial liquid fills the basin's inside (x +-2, y +-1.05, floor z 0) to its walls: emission keeps
    # a particle radius clear of them, so it starts at rest instead of falling into a gap.
    # Five actual solids create a basin; the domain allocation is not a wall.
    walls = [
        ("Basin Floor", (0, 0, -0.15), (4.5, 2.7, 0.3)),
        ("Basin Left", (-2.15, 0, 1), (0.3, 2.7, 2.3)),
        ("Basin Right", (2.15, 0, 1), (0.3, 2.7, 2.3)),
        ("Basin Front", (0, -1.2, 1), (4.5, 0.3, 2.3)),
        ("Basin Back", (0, 1.2, 1), (4.5, 0.3, 2.3)),
    ]
    for name, location, size in (walls if preset != "WAVE_TANK" else ()):
        obj = _box(name, colliders, origin + Vector(location), size)
        obj.fumaris_liquid.role = "COLLIDER"
        obj.display_type = "WIRE"
    if preset in REALTIME:
        _realtime(p)
    else:
        _detailed(p)
        preset = DETAILED[preset]
    if preset == "SPLASH":
        # A ball dropped into a pool throws up a crown, with spray, foam and bubbles.
        p.whitewater_generate = True
        emitter = _box("Initial Pool", emitters, origin + Vector((0, 0, 0.25)), (4.0, 2.1, 0.5))
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
    elif preset == "WAVE_TANK":
        # A paddle swept back and forth at the deep end sends waves up the beach.
        p.whitewater_generate = True
        tray = _tray("Wave Tank", colliders, origin)
        tray.fumaris_liquid.role = "COLLIDER"
        tray.fumaris_liquid.collider_margin = 0.05
        tray.display_type = "WIRE"
        # 0.35 high: no liquid is made within the tray's 0.05 m Margin, so this fills it to the level of before.
        emitter = _box("Initial Pool", emitters, origin + Vector((0, -0.05, 0.175)), (2.4, 2.9, 0.35))
        emitter.fumaris_liquid.emission_mode = "INITIAL"
        # Wall to wall and down on the tray floor's margin, so the water stays in front of it.
        paddle = _box("Wave Paddle", colliders, origin + Vector((0, 1.75, 0.45)), (2.38, 0.12, 0.8))
        paddle.fumaris_liquid.role = "COLLIDER"
        start = scene.frame_current
        for frame, y in ((start, 1.75), (start + 14, 1.45), (start + 28, 1.75)):
            paddle.location = origin + Vector((0, y, 0.45))
            paddle.keyframe_insert("location", index=1, frame=frame)
        for layer in paddle.animation_data.action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    for curve in bag.fcurves:
                        curve.modifiers.new("CYCLES")  # keeps making waves for as long as it plays
        paddle.location = origin + Vector((0, 1.75, 0.45))
    elif preset == "REALTIME_DAM_BREAK":
        # The block fills the basin's end from wall to wall, as in a dam-break
        # experiment, and runs into two pillars.
        emitter = _box("Initial Liquid", emitters, origin + Vector((-1.3, 0, 0.55)), (1.4, 2.1, 1.1))
        emitter.fumaris_liquid.emission_mode = "INITIAL"
        for name, location, size in (("Pillar Near", (0.35, -0.42, 0.5), (0.3, 0.3, 1.0)),
                                     ("Pillar Far", (1.0, 0.38, 0.4), (0.36, 0.36, 0.8))):
            pillar = _box(name, colliders, origin + Vector(location), size)
            pillar.fumaris_liquid.role = "COLLIDER"
    else:
        # A shallow existing pool makes splashes and recovery visible from the
        # first second without spending the user's iteration time filling a tank.
        pool = _box("Initial Pool", emitters, origin + Vector((0, 0, 0.175)), (4.0, 2.1, 0.35))
        pool.fumaris_liquid.role = "EMITTER"
        pool.fumaris_liquid.source_shape = "BOX"
        pool.fumaris_liquid.emission_mode = "INITIAL"
        pool.display_type = "WIRE"
        pool.hide_render = True
        emitter = _cylinder("Liquid Inflow", emitters, origin + Vector((0, 0, 2.6)), (0.55, 0.55, 0.30))
        emitter.fumaris_liquid.emission_mode = "CONTINUOUS"
        emitter.fumaris_liquid.velocity = (0, 0, -2)
        emitter.fumaris_liquid.emission_frequency = 48
    emitter.fumaris_liquid.role = "EMITTER"
    emitter.fumaris_liquid.source_shape = "BOX" if preset in {"REALTIME_DAM_BREAK", "SPLASH", "WAVE_TANK"} else "CYLINDER"
    emitter.display_type = "WIRE"
    emitter.hide_render = True
    display.ensure_outputs(scene, domain)
    for obj in context.selected_objects:
        obj.select_set(False)
    domain.select_set(True)
    context.view_layer.objects.active = domain
    return domain


def _droplets(context):
    """Millimetre droplets on an inclined plate; existing scenes stay untouched."""
    import math
    domain = create(context, "REALTIME_DAM_BREAK")
    p = domain.fumaris_liquid
    origin = context.scene.cursor.location.copy()
    for collection in (p.emitter_collection, p.collider_collection):
        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    domain.users_collection[0].name = "Aquaris Droplets"
    scale = max(1e-8, context.scene.unit_settings.scale_length)
    p.particle_radius = 0.0002 / scale
    p.mesh_cell_size = 0.00025 / scale
    p.mesh_cell_relative = False
    p.bounds_min = origin + Vector((-0.024, -0.024, -0.014)) / scale
    p.bounds_max = origin + Vector((0.024, 0.024, 0.02)) / scale
    # Drops a few cells across under-resolve their own tension: several times real water (0.073) holds them
    # together; the contact angle and grip let them bead, hang and slide (droplet benchmark, 2026-10-28).
    p.step_size, p.surface_tension = 1.0, 0.5
    p.surface_adhesion, p.contact_friction = 0.0, 0.0
    p.contact_angle, p.contact_grip = 110.0, 0.5
    p.flip_ratio = 0.3  # calm: small drops settle instead of jittering and shedding specks
    p.whitewater_generate = False
    p.droplet_scale = 1.0
    domain.empty_display_size = 0.01 / scale
    normal = Vector((0, 0.5, math.sqrt(0.75)))
    plate = _box("Inclined Droplet Plate", p.collider_collection,
                 origin - normal * (0.001 / scale), (0.04 / scale, 0.04 / scale, 0.002 / scale))
    plate.rotation_euler.x = -math.pi / 6
    plate.fumaris_liquid.role = "COLLIDER"
    for index, (x, radius) in enumerate(((-0.006, 0.0012), (0, 0.002), (0.007, 0.003))):
        location = origin + Vector((x / scale, 0, 0)) + normal * (0.6 * radius / scale)
        bead = _sphere(f"Droplet {index + 1}", p.emitter_collection, location, radius / scale)
        bead.fumaris_liquid.role = "EMITTER"
        bead.fumaris_liquid.source_shape = "SPHERE"
        bead.fumaris_liquid.emission_mode = "INITIAL"
        bead.display_type = "WIRE"
        bead.hide_render = True
    return domain


def present_droplets(context, domain):
    """Focus this view without deleting or hiding unrelated scene objects."""
    area = context.area
    if bpy.app.background or area is None or area.type != "VIEW_3D":
        return False
    region = next((r for r in area.regions if r.type == "WINDOW"), None)
    space = area.spaces.active
    if region is None or space.region_3d is None:
        return False
    objects = set(domain.users_collection[0].all_objects)
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        if obj.name in context.view_layer.objects:
            obj.select_set(True)
    if space.local_view is None:
        with context.temp_override(area=area, region=region):
            bpy.ops.view3d.localview(frame_selected=False)
    else:
        for obj in context.view_layer.objects:
            obj.local_view_set(space, obj in objects)
    for obj in context.selected_objects:
        obj.select_set(False)
    domain.select_set(True)
    context.view_layer.objects.active = domain
    scale = max(1e-8, context.scene.unit_settings.scale_length)
    view = space.region_3d
    view.view_location = context.scene.cursor.location.copy()
    view.view_distance = 0.055 / scale
    view.view_rotation = Vector((0.35, -1.0, 1.3)).to_track_quat("Z", "Y")
    view.view_perspective = "PERSP"
    space.clip_start = min(space.clip_start, 0.00005 / scale)
    area.tag_redraw()
    return True
