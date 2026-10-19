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


VISCOSITY_PRESETS = {"WATER": 0.0, "OIL": 1e-4, "HONEY": 5e-3, "CHOCOLATE": 2e-2, "THICK": 0.2}


def _viscosity_preset(self, context):
    if self.viscosity_preset in VISCOSITY_PRESETS:
        self.viscosity = VISCOSITY_PRESETS[self.viscosity_preset]


def _look_update(self, context):
    if self.role == "DOMAIN":
        from . import display
        display.refresh_look(self.id_data)


class LiquidProperties(bpy.types.PropertyGroup):
    role: EnumProperty(name="Liquid Role", items=[
        ("NONE", "None", "Not a liquid participant"),
        ("DOMAIN", "Domain", "Simulation settings and output owner"),
        ("EMITTER", "Emitter", "Create liquid particles"),
        ("COLLIDER", "Collider", "A sampled solid collision surface"),
        ("OUTFLOW", "Outflow", "Remove liquid inside the evaluated bounding box"),
        ("EFFECTOR", "Effector", "Force field. Not available in this version: a simulation with an enabled effector does not start"),
    ], default="NONE")
    enabled: BoolProperty(name="Enabled", default=True,
                          description="Animate to enable or disable this participant")
    domain_id: StringProperty(name="Cache Identity", options={"HIDDEN"})
    emitter_collection: PointerProperty(name="Emitters", type=bpy.types.Collection)
    collider_collection: PointerProperty(name="Colliders", type=bpy.types.Collection)
    effector_collection: PointerProperty(name="Effectors", type=bpy.types.Collection)
    outflow_collection: PointerProperty(name="Outflows", type=bpy.types.Collection)
    output_collection: PointerProperty(name="Output", type=bpy.types.Collection)
    surface_object: PointerProperty(type=bpy.types.Object)
    whitewater_object: PointerProperty(type=bpy.types.Object)
    particle_object: PointerProperty(type=bpy.types.Object)
    mist_object: PointerProperty(type=bpy.types.Object)
    viscosity_preset: EnumProperty(name="Viscosity Preset", update=_viscosity_preset, default="WATER", items=[
        ("WATER", "Water", "No viscosity: at the resolutions a grid this size reaches, water's own (1e-6 m²/s) has no visible effect"),
        ("OIL", "Oil", "1e-4 m²/s"),
        ("HONEY", "Honey", "5e-3 m²/s"),
        ("CHOCOLATE", "Molten Chocolate", "2e-2 m²/s"),
        ("THICK", "Thick", "0.2 m²/s: a slow, heavy flow"),
        ("CUSTOM", "Custom", "Set Viscosity yourself")])
    viscosity: FloatProperty(name="Viscosity", default=0, min=0, max=100, soft_max=1, precision=5, step=0.01,
                             description="Kinematic viscosity in m²/s (dynamic viscosity divided by density); 0 for water. "
                                         "Slows, smooths and sticks the liquid to colliders; it does not make it coil. Can be changed while running")
    surface_tension: FloatProperty(name="Surface Tension", default=0, min=0, max=1000, soft_max=50, precision=3, step=1,
                                   description="Surface tension in N/m (water 0.073), taken with water's density. At the sizes a grid "
                                               "this coarse resolves, water's own value shows little: raise it to make thin streams bead "
                                               "and small bodies round off. High values take more substeps. Can be changed while running")
    flip_ratio: FloatProperty(name="FLIP Ratio", default=0.95, min=0, max=1, precision=2, subtype="FACTOR",
                              description="How lively the liquid is. Each step a particle takes this share of its velocity from the "
                                          "change the grid computed (FLIP: keeps detail and energy, noisier) and the rest from the grid's "
                                          "own smooth velocity (PIC: calm, thicker-looking). 0.95 suits splashes; lower it for calm or "
                                          "slow liquid, raise it towards 1 for the most energetic spray. Can be changed while running")
    step_size: FloatProperty(name="Step Size", default=3.0, min=0.5, max=3.0, precision=1,
                             description="How far the fastest liquid may travel in one solver step, in grid cells. 3 is the fast "
                                         "preview: about half the steps, but the height a splash reaches on a wall and how the "
                                         "liquid settles then depend on Substeps. At 1 they no longer do, for about twice the "
                                         "time per frame. Draft sets 3, Final sets 1. Can be changed while running")
    particle_radius: FloatProperty(name="Particle Radius", default=0.04, min=0.005,
                                  max=2, subtype="DISTANCE",
                                  description="World-space sampling and solver radius; restart to change")
    substeps: IntProperty(name="Substeps", default=3, min=1, max=64,
                          description="Fewest steps the solver takes per frame; it adds more by itself when the liquid or a collider moves fast")
    gravity: FloatVectorProperty(name="Gravity", default=(0, 0, -9.81), size=3)
    bounds_min: FloatVectorProperty(name="Minimum", default=(-3, -2, -1), size=3,
                                   subtype="XYZ", description="World-space initial GPU region; expands automatically")
    bounds_max: FloatVectorProperty(name="Maximum", default=(3, 2, 4), size=3,
                                   subtype="XYZ", description="World-space initial GPU region; expands automatically")
    domain_walls: EnumProperty(name="Walls", options={'ENUM_FLAG'}, default=set(),
                               items=(("X_MIN", "-X", "Close the side at the region's minimum X"),
                                      ("X_MAX", "+X", "Close the side at the region's maximum X"),
                                      ("Y_MIN", "-Y", "Close the side at the region's minimum Y"),
                                      ("Y_MAX", "+Y", "Close the side at the region's maximum Y"),
                                      ("Z_MIN", "-Z", "Close the floor at the region's minimum Z"),
                                      ("Z_MAX", "+Z", "Close the top at the region's maximum Z")),
                               description="Sides of the region that are solid walls, as a collider would be; open sides "
                                           "let the liquid leave. Restart to change")
    remove_strays: BoolProperty(name="Remove Escaped Liquid", default=True,
                                description="Remove liquid that has fallen or been thrown well clear of every collider, emitter and the region above; it cannot come back, and would otherwise be simulated for as long as it keeps falling")
    auto_budgets: BoolProperty(name="Automatic Limits", default=True,
                               description="Let particles, collider samples and surface cells grow to the solver's own limits (4 million particles, 4 million collider samples, 64 million surface cells). Memory is taken as the simulation grows, so nothing is reserved in advance. Turn off to set smaller budgets that stop a runaway setup early")
    max_particles: IntProperty(name="Particle Budget", default=100000, min=128, max=8000000)
    max_boundary: IntProperty(name="Collider Sample Budget", default=200000, min=1000, max=4000000,
                              description="Collider surface samples accepted per frame; finer particles need more")
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
                                     description="Size the surface cells from the particle radius, so the surface keeps its level of detail when the resolution changes")
    mesh_cell_factor: FloatProperty(update=_surface_update, name="Surface Cell", default=1.0, min=0.5, max=4.0, precision=2,
                                    description="Surface cell size in particle radii. 1 resolves every particle. With Fast quality 2 meshes about three times faster and looks softer; Final and Smooth refine the surface back to one radius, so a larger cell does not speed them up")
    mesh_cell_size: FloatProperty(update=_surface_update, name="Surface Cell Size", default=0.04, min=0.005,
                                 subtype="DISTANCE", description="Smaller cells give a finer surface")
    max_mesh_cells: IntProperty(update=_surface_update, name="Surface Cell Budget", default=2000000,
                                min=1000, max=64000000)
    # Numbered as before the GPU item existed (0.2.12 and earlier files); files
    # saved by 0.2.13 or 0.2.14 used the order GPU, Fast, Final, Smooth.
    mesh_quality: EnumProperty(name="Mesh Quality", update=_surface_update, items=[
        ("GPU_SMOOTH", "GPU Smooth (preview)", "Built on the graphics card in milliseconds, then closed and smoothed as a field before it is meshed: resting liquid is flat, "
                                               "splashes stay connected sheets, slits and pores fill. Droplets are simple and Droplet Size does not apply; "
                                               "liquid closer together than about three particle radii joins; falls back to Fast when a frame does not fit", 4),
        ("GPU", "GPU Fast (preview)", "Built on the graphics card in milliseconds on round kernels: shows every particle-scale bump. "
                                      "Droplets are simple and Droplet Size does not apply; falls back to Fast when a frame is too large for it", 3),
        ("FAST", "Fast", "Smooth surface on the requested grid", 0),
        ("FINAL", "Final", "Refine coarse surface cells to retain thin features; uses more memory and time", 1),
        ("SMOOTH", "Smooth", "The density field is closed and smoothed before it is meshed: removes particle-scale bumps and fills slits; slower than Fast and joins liquid closer than about three particle radii", 2),
    ], default="FINAL")
    droplet_scale: FloatProperty(update=_surface_update, name="Droplet Size", default=0.5, min=0.05, max=1, subtype="FACTOR",
                                 description="Size of detached droplets of one to three particles in the surface mesh. "
                                             "1 keeps their particle volume; smaller values draw splashes as finer spray")
    surface_smoothing: FloatProperty(update=_surface_update, name="Surface Smoothing", default=0.4, min=0, max=1, subtype="FACTOR",
                                     description="Extra filtering of the surface mesh that removes particle-scale dimples "
                                                 "while restoring each body's volume; costs meshing time and softens fine detail")
    surface_velocity: BoolProperty(update=_surface_update, name="Surface Motion Vectors", default=True,
                                   description="Store a velocity for every surface vertex so renders can motion-blur "
                                               "the liquid; increases cache size by about a third")
    speed: FloatProperty(name="Simulation Speed", default=1, min=0.01, max=10)
    record_preview: BoolProperty(name="Record Preview", default=False,
                                 description="Write each simulated frame into the selected cache slot")
    cache_directory: StringProperty(name="Cache Directory", default="//fumaris_liquid_cache",
                                    subtype="DIR_PATH")
    cache_slot: StringProperty(name="Cache Slot", default="main")
    cache_playback: BoolProperty(name="Timeline Cache Playback", default=True)
    bake_start: IntProperty(name="Start", default=1, min=-100000, max=100000)
    bake_end: IntProperty(name="End", default=120, min=-100000, max=100000)
    display: EnumProperty(name="Display", items=[
        ("PREVIEW", "GPU Surface", "The meshed surface drawn straight to the viewport without updating Blender's mesh: the cheapest display. Switch to Surface, record or bake for renderable geometry"),
        ("SURFACE", "Surface", "Renderable liquid mesh and whitewater"),
        ("PARTICLES", "Particles", "Liquid particle diagnostic display"),
    ], default="PREVIEW", update=_display_update)
    status: StringProperty(name="Status", default="Ready", options={"SKIP_SAVE"})
    last_error: StringProperty(name="Error", options={"SKIP_SAVE"})
    runtime_info: StringProperty(name="Runtime", options={"SKIP_SAVE"})
    runtime_warning: StringProperty(name="Runtime Warning", options={"SKIP_SAVE"})
    # Emitter and collider settings.
    source_shape: EnumProperty(name="Source", items=[
        ("MESH", "Closed Mesh", "Fill the evaluated watertight mesh"),
        ("BOX", "Box", "Fill the object's transformed local bounding box"),
        ("SPHERE", "Sphere", "Fill an ellipsoid inside the object's local bounding box"),
        ("CYLINDER", "Cylinder", "Fill a transformed elliptical cylinder inside the local bounding box"),
        ("POINTS", "Geometry Nodes / Vertices", "Use evaluated positions and point attributes"),
        ("PARTICLES", "Particle System", "Use alive Blender particles"),
        ("COLLECTION", "Collection Meshes", "Fill closed meshes from the source collection"),
        ("VOLUME", "Volume / OpenVDB", "Convert a Blender Volume grid to a closed emission surface"),
        ("INSTANCES", "Geometry Nodes Instances", "Fill evaluated instance meshes without modifying the node graph"),
    ], default="MESH")
    source_collection: PointerProperty(name="Source Collection", type=bpy.types.Collection)
    emission_mode: EnumProperty(name="Emission", items=[
        ("INITIAL", "Initial Fill", "Emit once when first enabled"),
        ("CONTINUOUS", "Continuous Inflow", "Emit volume samples at the configured frequency"),
    ], default="INITIAL")
    emission_frequency: FloatProperty(name="Inflow Frequency", default=24, min=0.1, max=120,
                                      description="Volume emission ticks per simulation second")
    velocity: FloatVectorProperty(name="Initial Velocity", default=(0, 0, 0), size=3,
                                  description="World-space initial velocity")
    normal_velocity: FloatProperty(name="Normal Velocity", default=0, min=-100, max=100)
    inherited_motion: FloatProperty(name="Inherited Motion", default=1, min=0, max=10)
    mask_attribute: StringProperty(name="Mask Attribute", default="",
                                   description="Evaluated POINT-domain float attribute; samples below threshold do not emit")
    mask_threshold: FloatProperty(name="Mask Threshold", default=0.5, min=0, max=1)
    velocity_attribute: StringProperty(name="Velocity Attribute", default="velocity")
    volume_grid: StringProperty(name="Volume Grid", default="density")
    volume_threshold: FloatProperty(name="Volume Threshold", default=0.5, min=0,
                                    description="Isosurface level; use zero for a signed distance grid")
    volume_resolution: EnumProperty(name="Volume Resolution", items=[
        ("VOXEL_SIZE", "Voxel Size", "Resample the grid at the selected voxel size before conversion"),
        ("GRID", "Original Grid", "Convert at the volume file's native voxel resolution"),
    ], default="VOXEL_SIZE")
    volume_voxel_size: FloatProperty(name="Volume Voxel Size", default=0.065, min=0.005,
                                     subtype="DISTANCE")
    mask_type: EnumProperty(name="Texture Mask", items=[
        ("NONE", "None", "No additional texture mask"),
        ("TEXTURE", "Blender Texture", "Use native procedural texture intensity"),
        ("IMAGE", "Image", "Use projected image luminance multiplied by alpha"),
    ], default="NONE")
    mask_texture: PointerProperty(name="Mask Texture", type=bpy.types.Texture)
    mask_image: PointerProperty(name="Mask Image", type=bpy.types.Image)
    mask_image_offset: IntProperty(name="Sequence Offset", default=0,
                                   description="An image sequence mask uses the file numbered scene frame plus this offset; "
                                               "the first and last files are held outside the sequence")
    mask_coordinates: EnumProperty(name="Coordinates", items=[
        ("BOUNDS", "Source Bounds", "Map each evaluated geometry's local bounds to zero through one"),
        ("OBJECT", "Object", "Use source geometry local coordinates"),
        ("WORLD", "World", "Use world coordinates"),
    ], default="BOUNDS")
    mask_projection: EnumProperty(name="Image Projection", items=[
        ("XY", "XY", "Project onto local texture X/Y"),
        ("XZ", "XZ", "Project onto local texture X/Z"),
        ("YZ", "YZ", "Project onto local texture Y/Z"),
    ], default="XY")
    mask_scale: FloatVectorProperty(name="Mask Scale", default=(1, 1, 1), size=3)
    mask_offset: FloatVectorProperty(name="Mask Offset", default=(0, 0, 0), size=3)


def register():
    bpy.utils.register_class(LiquidProperties)
    bpy.types.Object.fumaris_liquid = PointerProperty(type=LiquidProperties)
    bpy.types.Scene.fumaris_liquid_domain = PointerProperty(name="Liquid Domain", type=bpy.types.Object)


def unregister():
    del bpy.types.Scene.fumaris_liquid_domain
    del bpy.types.Object.fumaris_liquid
    bpy.utils.unregister_class(LiquidProperties)
