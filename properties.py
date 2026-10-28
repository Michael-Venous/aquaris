"""Independent saved scene settings; never access Fumaris smoke properties."""

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)


def _display_update(self, context):
    if self.role == "DOMAIN":
        from . import display
        display.refresh_mode(context.scene, self.id_data)


def _surface_update(self, context):
    if self.role == "DOMAIN":
        from . import session
        session.surface_changed(self.id_data)


VISCOSITY_PRESETS = {"WATER": 0.0, "SMALL_WATER": 0.0, "OIL": 1e-4, "HONEY": 5e-3, "CHOCOLATE": 2e-2, "THICK": 0.2}
# Water at two scales. Drops only a few cells across under-resolve their own tension, so small-scale water uses several
# times water's real 0.073 N/m, a beading contact angle and some grip (droplet benchmark, 2026-10-28); large water
# needs no surface tension at all and keeps the solver at full speed.
# Small drops also need calmer particles: at FLIP 0.95 a settled 2 mm drop kept moving at 83 mm/s and shed 9 % of its
# particles as specks; at 0.3 it moved at 7 mm/s and shed under 1 % (test drop, 2026-10-28).
LIQUID_SURFACE = {"WATER": dict(surface_tension=0.0, contact_angle=90.0, contact_grip=0.0, flip_ratio=0.95),
                  "SMALL_WATER": dict(surface_tension=0.5, contact_angle=110.0, contact_grip=0.5, flip_ratio=0.3,
                                      surface_adhesion=0.0, contact_friction=0.0)}


def _viscosity_preset(self, context):
    if self.viscosity_preset in VISCOSITY_PRESETS:
        self.viscosity = VISCOSITY_PRESETS[self.viscosity_preset]
    for name, value in LIQUID_SURFACE.get(self.viscosity_preset, {}).items():
        setattr(self, name, value)


def _radius_unit(scale):
    """(property, label) for showing Particle Radius in a readable unit, as Blender shows a light's radius:
    millimetres for small radii, centimetres, then metres."""
    def get(self):
        return self.particle_radius * max(self.id_data.users_scene[0].unit_settings.scale_length if self.id_data.users_scene else 1.0, 1e-12) / scale
    def set(self, value):
        unit = max(self.id_data.users_scene[0].unit_settings.scale_length if self.id_data.users_scene else 1.0, 1e-12)
        self.particle_radius = value * scale / unit
    return get, set


_mm_get, _mm_set = _radius_unit(0.001)
_cm_get, _cm_set = _radius_unit(0.01)
_m_get, _m_set = _radius_unit(1.0)


def _range_update(self, context):
    # A Start or End set (by a script: the panel shows them only with Use Timeline off) is the range wanted.
    if self.use_scene_frame_range:
        self.use_scene_frame_range = False


def _role_update(self, context):
    # A new domain follows the timeline, so its range matches the scene without editing (as in Fumaris). A domain
    # whose Start and End were already changed keeps its own range.
    if (self.role == "DOMAIN" and not self.use_scene_frame_range
            and self.bake_start == 1 and self.bake_end == 120):
        self.use_scene_frame_range = True


def _look_update(self, context):
    if self.role == "DOMAIN":
        from . import display
        display.refresh_look(self.id_data)


class LiquidProperties(bpy.types.PropertyGroup):
    role: EnumProperty(name="Liquid Role", items=[
        ("NONE", "None", "This object takes no part in the liquid simulation"),
        ("DOMAIN", "Domain", "The simulation itself: holds its settings and owns the liquid, surface and whitewater objects it creates"),
        ("EMITTER", "Emitter", "Creates liquid: fills its shape once, or keeps pouring liquid in"),
        ("COLLIDER", "Collider", "A solid the liquid cannot enter: walls, floors, containers, moving objects"),
        ("OUTFLOW", "Outflow", "Deletes any liquid that enters this object's bounding box (a drain or a sink)"),
        ("EFFECTOR", "Effector", "A force field around this object that pushes the liquid: force, wind, vortex or turbulence"),
    ], default="NONE", update=_role_update,
        description="What this object does in an Aquaris simulation. A domain holds the simulation's settings; emitters, colliders, "
                    "outflows and effectors take part when they are in the matching collection of a domain")
    enabled: BoolProperty(name="Enabled", default=True,
                          description="Animate to enable or disable this participant")
    collider_margin: FloatProperty(name="Margin", default=0.0, min=0.0, soft_max=0.5, subtype="DISTANCE", unit="LENGTH",
                                   description="Thickness added to this collider's surface. A closed mesh grows outward by "
                                               "this much; an open mesh (a plane, a sheet) becomes a slab this thick on each "
                                               "side, so liquid is held from both sides instead of only in front of its "
                                               "faces. Thinner than about a grid cell, the solver widens it to one cell")
    domain_id: StringProperty(name="Cache Identity", options={"HIDDEN"})
    emitter_collection: PointerProperty(name="Emitters", type=bpy.types.Collection,
                                        description="Collection whose objects with the Emitter role create this simulation's liquid")
    collider_collection: PointerProperty(name="Colliders", type=bpy.types.Collection,
                                         description="Collection whose objects with the Collider role are solid to this simulation's liquid")
    effector_collection: PointerProperty(name="Effectors", type=bpy.types.Collection,
                                         description="Collection whose objects with the Effector role push this simulation's liquid")
    outflow_collection: PointerProperty(name="Outflows", type=bpy.types.Collection,
                                        description="Collection whose objects with the Outflow role delete this simulation's liquid")
    output_collection: PointerProperty(name="Output", type=bpy.types.Collection)
    surface_object: PointerProperty(type=bpy.types.Object)
    whitewater_object: PointerProperty(type=bpy.types.Object)
    particle_object: PointerProperty(type=bpy.types.Object)
    mist_object: PointerProperty(type=bpy.types.Object)
    viscosity_preset: EnumProperty(name="Liquid Preset", update=_viscosity_preset, default="WATER", items=[
        ("WATER", "Water", "Water at everyday scale: no viscosity (water's own 1e-6 m²/s has no visible effect at these "
                           "resolutions) and no surface tension, which keeps the solver fast"),
        ("SMALL_WATER", "Small-Scale Water", "Water drops and splashes of a few millimetres: surface tension holds drops "
                                             "together, they bead on surfaces (Contact Angle) and hang on them (Contact Grip). "
                                             "Use a Particle Radius of about a tenth of the drop radius"),
        ("OIL", "Oil", "1e-4 m²/s"),
        ("HONEY", "Honey", "5e-3 m²/s"),
        ("CHOCOLATE", "Molten Chocolate", "2e-2 m²/s"),
        ("THICK", "Thick", "0.2 m²/s: a slow, heavy flow"),
        ("CUSTOM", "Custom", "Set Viscosity yourself")])
    viscosity: FloatProperty(name="Viscosity", default=0, min=0, max=100, soft_max=1, precision=5, step=0.01,
                             description="How thick the liquid is: 0 for water, higher for oil, honey or molten chocolate (pick a "
                                         "preset above to start). Thick liquid flows slowly, smooths out and sticks to colliders. "
                                         "In m²/s (kinematic viscosity). Can be changed while playing")
    surface_tension: FloatProperty(name="Surface Tension", default=0, min=0, max=1000, soft_max=50, precision=3, step=1,
                                   description="How strongly the liquid pulls itself together: thin streams break into beads and small "
                                               "bodies round off. Water's real value (0.073) is too small to see at most particle sizes, "
                                               "so raise it until the effect shows. High values slow the simulation. Can be changed while playing")
    surface_adhesion: FloatProperty(name="Surface Adhesion", default=0, min=0, max=1000, soft_max=1, precision=4,
                                   description="Short-range attraction to colliders, in N/m at water density. Helps small droplets cling; "
                                               "increase gradually alongside surface tension. Acts within two grid cells, so resolve the droplet "
                                               "with several cells. This is an artistic wetting strength, not a measured contact angle. "
                                               "0 keeps the previous behavior. Can be changed while playing")
    contact_friction: FloatProperty(name="Contact Friction", default=0, min=0, max=1, subtype="FACTOR",
                                   description="Resistance to sliding relative to a collider, limited by contact and adhesion forces. "
                                               "0 allows water to slide freely; higher values help small drops cling on tilted surfaces. "
                                               "Does not affect liquid away from a collider. Can be changed while playing")
    contact_angle: FloatProperty(name="Contact Angle", default=90, min=0, max=180, precision=0, step=100,
                                 description="With Surface Tension: the angle at which the liquid meets a collider, in degrees. "
                                             "Low values wet and spread (clean glass about 30-70), 90 is neutral, high values bead up "
                                             "(a waxy leaf about 110-150). At small drop sizes the shape tops out near a hemisphere. "
                                             "Can be changed while playing")
    contact_grip: FloatProperty(name="Contact Grip", default=0, min=0, max=1, subtype="FACTOR",
                                description="With Surface Tension: how strongly liquid touching a collider holds against sliding along "
                                            "it, so small drops hang on a window while heavy ones slide. Scales with Surface Tension. "
                                            "0 lets it slide freely. Can be changed while playing")
    flip_ratio: FloatProperty(name="FLIP Ratio", default=0.95, min=0, max=1, precision=2, subtype="FACTOR",
                              description="How lively the liquid is. Higher keeps more splash, spray and small detail (0.95 suits "
                                          "most water); lower gives calmer, smoother, thicker-looking motion. Can be changed while playing")
    step_size: FloatProperty(name="Step Size", default=3.0, min=0.5, max=3.0, precision=1,
                             description="Speed against accuracy. How far the fastest liquid may move in one solver step, in grid "
                                         "cells: 3 takes about half the steps of 1 and is the fast preview; 1 is the accurate "
                                         "setting for final results (splashes run up walls and settle as they should). "
                                         "Can be changed while playing")
    collider_step: FloatProperty(name="Collider Step", default=1.8, min=0.25, max=3.0, precision=2,
                                 description="Speed against accuracy for moving colliders. How far a collider near the liquid may "
                                             "move in one solver step, in grid cells: fast colliders force extra steps. 1.8 suits "
                                             "most scenes; lower it to 0.9 for the cleanest impacts (a ball dropped into water "
                                             "throwing up an intact crown), or when a fast collider presses liquid through a "
                                             "thin floor. Higher values are faster but coarser where colliders strike liquid. "
                                             "Can be changed while playing")
    particle_radius_mm: FloatProperty(name="Particle Radius (mm)", get=_mm_get, set=_mm_set, min=0.01, soft_min=0.05, soft_max=100,
                                      precision=3, step=1, description="Particle Radius in millimetres")
    particle_radius_cm: FloatProperty(name="Particle Radius (cm)", get=_cm_get, set=_cm_set, min=0.001, soft_min=0.1, soft_max=100,
                                      precision=3, step=1, description="Particle Radius in centimetres")
    particle_radius_m: FloatProperty(name="Particle Radius (m)", get=_m_get, set=_m_set, min=0.0001, soft_max=100,
                                     precision=3, step=1, description="Particle Radius in metres")
    show_limits: BoolProperty(name="Limits", get=lambda self: not self.auto_budgets,
                              set=lambda self, value: setattr(self, "auto_budgets", not value),
                              description="Set your own particle, collider and surface limits, which stop a setup that is "
                                          "accidentally far too big before it uses all your graphics memory. Off (default): the "
                                          "simulation grows as large as it needs")
    particle_radius: FloatProperty(name="Particle Radius", default=0.04, min=0.0001, max=100, soft_min=0.005, soft_max=2, subtype="DISTANCE",
                                   description="The simulation's resolution: the size of one liquid particle. Halving it shows finer "
                                               "detail but takes about eight times the particles and much longer. Takes effect when "
                                               "the simulation is started again")
    substeps: IntProperty(name="Substeps", default=1, min=1, max=64,
                          description="The fewest solver steps per frame. 1 is right for almost everything: the solver adds steps "
                                      "by itself when liquid or a collider moves fast. Raise it only to smooth very violent motion. "
                                      "Can be changed while playing")
    gravity: FloatVectorProperty(name="Gravity", default=(0, 0, -9.81), size=3, subtype="ACCELERATION",
                                 description="Acceleration pulling the liquid, in m/s² (Earth: 0, 0, -9.81). Can be changed while playing")
    bounds_min: FloatVectorProperty(name="Minimum", default=(-3, -2, -1), size=3, subtype="XYZ",
                                    description="One corner of the region the simulation starts with. The region grows by itself "
                                                "to follow the liquid, so this only needs to roughly cover where the liquid starts. "
                                                "With Walls, it is also where the walls are")
    bounds_max: FloatVectorProperty(name="Maximum", default=(3, 2, 4), size=3, subtype="XYZ",
                                    description="The opposite corner of the region the simulation starts with. The region grows by "
                                                "itself to follow the liquid. With Walls, it is also where the walls are")
    domain_walls: EnumProperty(name="Walls", options={'ENUM_FLAG'}, default=set(),
                               items=(("X_MIN", "-X", "Make the region's -X side a solid wall"),
                                      ("X_MAX", "+X", "Make the region's +X side a solid wall"),
                                      ("Y_MIN", "-Y", "Make the region's -Y side a solid wall"),
                                      ("Y_MAX", "+Y", "Make the region's +Y side a solid wall"),
                                      ("Z_MIN", "-Z", "Make the region's bottom a solid floor"),
                                      ("Z_MAX", "+Z", "Make the region's top a solid lid")),
                               description="Sides of the region (Minimum to Maximum) that act as solid walls, like an invisible "
                                           "box. Liquid flows out through open sides. Takes effect when the simulation is started again")
    remove_strays: BoolProperty(name="Remove Escaped Liquid", default=True,
                                description="Delete liquid that has fallen or flown far away from everything in the scene. It could "
                                            "never come back, and would otherwise keep costing time as it falls forever")
    auto_budgets: BoolProperty(name="Automatic Limits", default=True,
                               description="Let the simulation grow as large as it needs (up to 4 million particles). Turn off to set "
                                           "your own limits, which stop a setup that is accidentally far too big before it uses all "
                                           "your graphics memory")
    max_particles: IntProperty(name="Particle Budget", default=100000, min=128, max=8000000,
                               description="The most liquid particles allowed; the simulation stops with a message when it needs more")
    max_boundary: IntProperty(name="Collider Sample Budget", default=200000, min=1000, max=8000000,
                              description="The most points used to describe collider surfaces; large colliders and small particles need more")
    whitewater_generate: BoolProperty(name="Generate Whitewater", default=False,
                                      description="Give off spray, foam and bubbles where liquid traps air or a wave crest breaks, "
                                                  "from each frame's liquid particles. Can be changed while running")
    whitewater_amount: FloatProperty(name="Amount", default=1, min=0, max=100, soft_max=10,
                                     description="How many whitewater particles the liquid gives off; 1 is the default rate")
    whitewater_lifetime: FloatProperty(name="Foam Lifetime", default=3, min=0.01, max=600, subtype="TIME_ABSOLUTE", unit="TIME_ABSOLUTE",
                                       description="Mean seconds a foam particle lasts on the surface before it dissolves (each one lasts half to one and a half times this)")
    whitewater_buoyancy: FloatProperty(name="Bubble Rise", default=2, min=0, max=20,
                                       description="Upward acceleration of bubbles under the surface, in units of gravity")
    whitewater_drag: FloatProperty(name="Bubble Drag", default=0.5, min=0, max=1,
                                   description="How strongly bubbles are carried along with the liquid around them, per frame")
    max_whitewater: IntProperty(name="Whitewater Budget", default=2000000, min=0, max=16000000,
                                description="Most whitewater particles kept at once; generation slows to stay within it")
    whitewater: BoolProperty(name="Show Whitewater", default=True, update=_display_update,
                             description="Show the spray, foam and bubbles (generated, or stored in a cache)")
    whitewater_size: FloatProperty(name="Particle Size", default=2, min=0.1, max=20, update=_look_update,
                                   description="Rendered size of spray, foam and bubble particles. 1 is the size used before Aquaris 0.2.8; larger particles read as denser foam")
    whitewater_mist: FloatProperty(name="Mist", default=0, min=0, max=100, update=_look_update,
                                   description="Density of a soft scattering volume built from spray and foam, for falls and heavy spray. 0 disables it. Needs Light Paths > Volume bounces above zero in Cycles to render white")
    whitewater_mist_radius: FloatProperty(name="Mist Radius", default=4, min=1, max=20, update=_look_update,
                                          description="Reach of each whitewater particle's mist, in liquid particle radii")
    mesh_cell_relative: BoolProperty(update=_surface_update, name="Follow Particle Radius", default=True,
                                     description="Size the surface detail from the particle radius, so changing the radius keeps the "
                                                 "surface looking the same. Turn off to set an absolute Surface Cell Size")
    mesh_cell_factor: FloatProperty(update=_surface_update, name="Surface Cell", default=1.0, min=0.5, max=4.0, precision=2,
                                    description="Surface detail, in particle radii: 1 shows every particle's worth of detail; larger "
                                                "values mesh faster and look softer. 1 gives fine detail; 1.5 is faster")
    mesh_cell_size: FloatProperty(update=_surface_update, name="Surface Cell Size", default=0.04, min=0.0001, soft_min=0.005, subtype="DISTANCE",
                                  description="Surface detail as a distance: smaller is finer and slower to mesh")
    max_mesh_cells: IntProperty(update=_surface_update, name="Surface Cell Budget", default=2000000, min=1000, max=64000000,
                                description="The most surface cells meshed per frame; very large or very fine surfaces need more")
    # Numbered as before the GPU item existed (0.2.12 and earlier files); files
    # saved by 0.2.13 or 0.2.14 used the order GPU, Fast, Final, Smooth.
    mesh_quality: EnumProperty(name="Mesh Quality", update=_surface_update, items=[
        ("GPU_SMOOTH", "Smooth", "Clean, smooth liquid made on the graphics card in milliseconds: flat at rest, connected sheets in "
                                 "splashes. Right for previews and final renders", 4),
        ("GPU", "Detailed", "Made on the graphics card in milliseconds, keeping every particle-sized bump and ripple: looks "
                            "busier and more textured than Smooth", 3),
    ], default="GPU_SMOOTH", description="How the liquid surface is built from the particles for baked, recorded and Surface output")
    droplet_scale: FloatProperty(update=_surface_update, name="Droplet Size", default=0.5, min=0.05, max=1, subtype="FACTOR",
                                 description="Size of small flying droplets in the surface mesh. 1 keeps their real volume; "
                                             "smaller values draw splashes as finer spray")
    surface_contact: BoolProperty(update=_surface_update, name="Mesh Contact", default=True,
                                  description="Mesh-only contact; does not attract or hold particles. Let the liquid's surface meet colliders it rests against (a glass, a floor, a tank "
                                              "wall) instead of standing off them by about a particle radius. Without it, water in "
                                              "a glass shows a thin uneven layer of air against the glass")
    surface_smoothing: FloatProperty(update=_surface_update, name="Surface Smoothing", default=1.0, min=0, max=1, subtype="FACTOR",
                                     description="Smoothing of the liquid surface: higher removes small dimples and lumps but "
                                                 "softens fine detail and takes a little longer")
    surface_velocity: BoolProperty(update=_surface_update, name="Motion Blur Data", default=True,
                                   description="Save how fast each point of the surface moves in baked and recorded frames, so the "
                                               "liquid gets motion blur in renders (turn on Motion Blur in the render settings). It "
                                               "has no visible effect in the viewport. Makes the cache about a third larger")
    speed: FloatProperty(name="Simulation Speed", default=1, min=0.01, max=10,
                         description="How fast time passes in the simulation: 0.5 is slow motion, 2 is double speed. Can be changed while playing")
    preview_rate: EnumProperty(name="Preview Rate", default="SCENE", items=[
        # (numbered as in 0.3.17, which also had "24 fps" as 1: legacy_settings turns that into the scene rate)
        ("SCENE", "Scene Frame Rate", "Play the live preview at the scene's frame rate, so it moves in real time; the graphics card "
                                      "rests between frames", 2),
        ("UNLIMITED", "As Fast As Possible", "Show live frames as soon as they are simulated, even faster than real time", 0),
    ], description="How fast the live preview plays (bakes always run at full speed)")
    record_preview: BoolProperty(name="Record Preview", default=False,
                                 description="Save every frame of the live preview to the cache, so you can play it back, scrub "
                                             "the timeline or render it without baking again")
    cache_directory: StringProperty(name="Cache Directory", default="//fumaris_liquid_cache", subtype="DIR_PATH",
                                    description="Folder for baked and recorded frames. // uses the saved .blend folder; unsaved files use your user cache folder")
    cache_slot: StringProperty(name="Cache Slot", default="main",
                               description="Name of this bake inside the cache folder. Use different names to keep several "
                                           "versions and switch between them")
    cache_playback: BoolProperty(name="Timeline Cache Playback", default=True,
                                 description="Show the saved frame for the current timeline frame when you scrub or play the timeline")
    compress_cache: BoolProperty(name="Compress Cache", default=True,
                                 description="Write baked frames compressed: about half the disk space, for a little time to "
                                             "write and read each frame. Caches either way can be read")
    cache_particles: BoolProperty(name="Cache Particles", default=True,
                                  description="Keep the liquid's particles in the baked frames. Needed for the Particles display, "
                                              "Rebuild Surface and particle or volume export; off, a bake that only renders the "
                                              "surface takes about 40 % less space")
    bake_start: IntProperty(name="Start", default=1, min=-100000, max=100000, description="First frame to bake",
                            update=_range_update)
    bake_end: IntProperty(name="End", default=120, min=-100000, max=100000, description="Last frame to bake",
                          update=_range_update)
    use_scene_frame_range: BoolProperty(name="Use Timeline", default=False,
                                        description="Bake the scene's timeline range, starting Warmup frames early, "
                                                    "instead of a separate Start and End. On for new domains")
    warmup_frames: IntProperty(name="Warmup", default=0, min=0, soft_max=100,
                               description="Frames simulated before the timeline's start, so the liquid has already "
                                           "settled and is moving on the first visible frame")
    display: EnumProperty(name="Display", items=[
        ("PREVIEW", "GPU Surface", "Draw the liquid surface straight from the graphics card: the fastest view, but it is not "
                                   "Blender geometry and does not render. Bake or switch to Surface for renderable liquid"),
        ("SURFACE", "Surface", "Real Blender meshes for the liquid and whitewater, with their materials: what renders"),
        ("PARTICLES", "Particles", "Show the simulation's particles instead of a surface, to see what the solver is doing"),
    ], default="PREVIEW", update=_display_update, description="What the viewport shows of the liquid")
    status: StringProperty(name="Status", default="Ready", options={"SKIP_SAVE"})
    progress: StringProperty(name="Progress", options={"SKIP_SAVE"})  # a running bake's frame, time so far and left
    bake_summary: StringProperty(name="Last Bake")  # how the last bake of this domain went (kept with the file)
    last_error: StringProperty(name="Error", options={"SKIP_SAVE"})
    runtime_info: StringProperty(name="Runtime", options={"SKIP_SAVE"})
    runtime_warning: StringProperty(name="Runtime Warning", options={"SKIP_SAVE"})
    # Emitter and collider settings.
    source_shape: EnumProperty(name="Source", items=[
        ("MESH", "Closed Mesh", "Fill the inside of this mesh (it must be closed, without holes)"),
        ("BOX", "Box", "Fill this object's bounding box"),
        ("SPHERE", "Sphere", "Fill a sphere (or ellipsoid) fitted inside this object's bounding box"),
        ("CYLINDER", "Cylinder", "Fill a cylinder fitted inside this object's bounding box"),
        ("POINTS", "Geometry Nodes / Vertices", "Emit liquid at this object's vertices or Geometry Nodes points"),
        ("PARTICLES", "Particle System", "Emit liquid at this object's Blender particles"),
        ("COLLECTION", "Collection Meshes", "Fill the closed meshes in the Source Collection"),
        ("VOLUME", "Volume / OpenVDB", "Fill the inside of a Blender Volume object or OpenVDB file"),
        ("INSTANCES", "Geometry Nodes Instances", "Fill the meshes instanced by this object's Geometry Nodes"),
    ], default="MESH", description="What shape of liquid this emitter makes")
    source_collection: PointerProperty(name="Source Collection", type=bpy.types.Collection,
                                       description="Collection whose closed meshes this emitter fills with liquid")
    emission_mode: EnumProperty(name="Emission", items=[
        ("INITIAL", "Initial Fill", "Fill the shape with liquid once, when the simulation starts or the emitter is enabled"),
        ("CONTINUOUS", "Continuous Inflow", "Keep adding liquid while the emitter is enabled, like a tap or a hose"),
    ], default="INITIAL", description="Whether the emitter fills once or keeps pouring")
    emission_frequency: FloatProperty(name="Inflow Frequency", default=24, min=0.1, max=480,
                                      description="How many times per second a continuous emitter at least adds a layer of liquid. "
                                                  "It adds them more often by itself when the liquid would otherwise leave the "
                                                  "emitter in slices (a fast flow from a short emitter)")
    velocity: FloatVectorProperty(name="Initial Velocity", default=(0, 0, 0), size=3, subtype="VELOCITY",
                                  description="Speed and direction the new liquid starts with, in m/s")
    normal_velocity: FloatProperty(name="Normal Velocity", default=0, min=-100, max=100,
                                   description="Extra speed out of (positive) or into (negative) the emitter's surface, in m/s")
    inherited_motion: FloatProperty(name="Inherited Motion", default=1, min=0, max=10,
                                    description="How much of the emitter's own movement the new liquid takes along: 1 is all of it, "
                                                "0 none (a moving emitter then leaves liquid behind at rest)")
    mask_attribute: StringProperty(name="Mask Attribute", default="",
                                   description="Name of a point float attribute: points whose value is below Mask Threshold emit "
                                               "no liquid. Leave empty to emit everywhere")
    mask_threshold: FloatProperty(name="Mask Threshold", default=0.5, min=0, max=1,
                                  description="Points whose mask value is below this emit no liquid")
    velocity_attribute: StringProperty(name="Velocity Attribute", default="velocity",
                                       description="Name of a point vector attribute that sets each point's starting velocity")
    volume_grid: StringProperty(name="Volume Grid", default="density",
                                description="Which grid of the volume to fill from (usually density)")
    volume_threshold: FloatProperty(name="Volume Threshold", default=0.5, min=0,
                                    description="Isosurface level; use zero for a signed distance grid")
    volume_resolution: EnumProperty(name="Volume Resolution", items=[
        ("VOXEL_SIZE", "Voxel Size", "Resample the grid at the selected voxel size before conversion"),
        ("GRID", "Original Grid", "Convert at the volume file's native voxel resolution"),
    ], default="VOXEL_SIZE", description="At what resolution the volume is read")
    volume_voxel_size: FloatProperty(name="Volume Voxel Size", default=0.065, min=0.005,
                                     subtype="DISTANCE",
                                     description="Size of the voxels the volume is resampled to before it is turned into a "
                                                 "surface; smaller is more exact and slower")
    mask_type: EnumProperty(name="Texture Mask", items=[
        ("NONE", "None", "No additional texture mask"),
        ("TEXTURE", "Blender Texture", "Use native procedural texture intensity"),
        ("IMAGE", "Image", "Use projected image luminance multiplied by alpha"),
    ], default="NONE", description="Switch emission on and off across the source with a texture or an image: liquid is made "
                                   "only where the mask passes Mask Threshold")
    mask_texture: PointerProperty(name="Mask Texture", type=bpy.types.Texture,
                                  description="Procedural texture whose intensity is the mask")
    mask_image: PointerProperty(name="Mask Image", type=bpy.types.Image,
                                description="Image (or numbered image sequence) whose brightness, multiplied by its alpha, is the mask")
    mask_image_offset: IntProperty(name="Sequence Offset", default=0,
                                   description="An image sequence mask uses the file numbered scene frame plus this offset; "
                                               "the first and last files are held outside the sequence")
    mask_coordinates: EnumProperty(name="Coordinates", items=[
        ("BOUNDS", "Source Bounds", "Map each evaluated geometry's local bounds to zero through one"),
        ("OBJECT", "Object", "Use source geometry local coordinates"),
        ("WORLD", "World", "Use world coordinates"),
    ], default="BOUNDS", description="The space the texture or image mask is laid out in")
    mask_projection: EnumProperty(name="Image Projection", items=[
        ("XY", "XY", "Project onto local texture X/Y"),
        ("XZ", "XZ", "Project onto local texture X/Z"),
        ("YZ", "YZ", "Project onto local texture Y/Z"),
    ], default="XY", description="Which pair of axes the image is projected along")
    mask_scale: FloatVectorProperty(name="Mask Scale", default=(1, 1, 1), size=3,
                                    description="Scale of the mask along each axis of the chosen coordinates")
    mask_offset: FloatVectorProperty(name="Mask Offset", default=(0, 0, 0), size=3,
                                     description="Shift of the mask along each axis of the chosen coordinates")
    # Effectors: the same names as before 0.3.0, so scenes made then keep their fields.
    effector_kind: EnumProperty(name="Force", items=[
        ("FORCE", "Force", "A uniform acceleration (Acceleration times Strength) within the radius"),
        ("WIND", "Wind", "Acceleration along the object's local Z within the radius"),
        ("VORTEX", "Vortex", "Swirl around the object's local Z within the radius; Strength is the acceleration along the swirl"),
        ("TURBULENCE", "Turbulence", "Moving, spatially varying gusts a quarter of the radius across"),
    ], default="WIND", description="The kind of force this effector applies to liquid within its Radius")
    force_vector: FloatVectorProperty(name="Acceleration", default=(0, 0, 1), size=3, subtype="ACCELERATION",
                                      description="Direction (and relative size) of a uniform force, in world space; "
                                                  "multiplied by Strength")
    force_strength: FloatProperty(name="Strength", default=5, min=-1000, max=1000, soft_min=-50, soft_max=50,
                                  description="Acceleration in m/s² (gravity is 9.81); negative reverses the field")
    force_radius: FloatProperty(name="Radius", default=2, min=0.01, subtype="DISTANCE",
                                description="Reach of the field around the object: full strength to three quarters of it, fading out by the radius")


# Stored enum numbers of settings that no longer exist, and what they become: mesh_quality FAST (0), FINAL (1) and
# SMOOTH (2) (the processor's meshers, which looked like the graphics card's) and preview_rate FPS_24 (1, 0.3.17).
_LEGACY = {"mesh_quality": {0: "GPU", 1: "GPU_SMOOTH", 2: "GPU_SMOOTH"}, "preview_rate": {1: "SCENE"}}


@bpy.app.handlers.persistent
def legacy_settings(*_):
    for obj in bpy.data.objects:
        props = getattr(obj, "fumaris_liquid", None)
        if props is None:
            continue
        for name, mapping in _LEGACY.items():
            stored = props.get(name)
            if isinstance(stored, int) and stored in mapping:
                setattr(props, name, mapping[stored])


def register():
    bpy.utils.register_class(LiquidProperties)
    bpy.app.handlers.load_post.append(legacy_settings)
    bpy.types.Object.fumaris_liquid = PointerProperty(type=LiquidProperties)
    bpy.types.Scene.fumaris_liquid_domain = PointerProperty(name="Liquid Domain", type=bpy.types.Object)


def unregister():
    if legacy_settings in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(legacy_settings)
    del bpy.types.Scene.fumaris_liquid_domain
    del bpy.types.Object.fumaris_liquid
    bpy.utils.unregister_class(LiquidProperties)
