"""Independent saved scene settings; never access Fumaris smoke properties."""

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty,
                       StringProperty)


def _display_update(self, context):
    if self.role == "DOMAIN":
        from . import display
        display.refresh_mode(context.scene, self.id_data)


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
        ("EFFECTOR", "Effector", "Apply a localized acceleration"),
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
    # Explicit numbers: Blender stores an enum by number, and an item added
    # in front would otherwise change what saved files mean.
    solver: EnumProperty(name="Solver", items=[
        ("PBF", "Particles", "Position-based particle solver. Supports every feature: viscosity, surface tension, whitewater, effectors", 0),
        ("FLIP", "Grid (experimental)", "Particles moved by a pressure solve on a grid. About ten times faster per particle and forms crowns "
                                        "from solid impacts, at half the detail per particle. No viscosity, surface tension, whitewater or "
                                        "effectors yet; colliders must be meshes with outward face normals", 1),
    ], default="PBF", description="How the liquid is simulated; restart to change")
    jet_limit: FloatProperty(name="Jet Limit", default=2.0, min=0, max=10, precision=2,
                             description="Grid solver: fastest speed allowed, as a multiple of what the liquid could reach from "
                                         "its fall and the speed it was emitted or pushed at. Thin jets focus energy and can "
                                         "shoot far above a splash; lower values calm them, 0 leaves them unlimited")
    particle_radius: FloatProperty(name="Particle Radius", default=0.04, min=0.005,
                                  max=2, subtype="DISTANCE",
                                  description="World-space sampling and solver radius; restart to change")
    iterations: IntProperty(name="Pressure Iterations", default=6, min=1, max=100)
    substeps: IntProperty(name="Substeps", default=3, min=1, max=64)
    viscosity: FloatProperty(name="Velocity Smoothing", default=0.02, min=0, max=1,
                             description="Nebula velocity smoothing coefficient; higher values resist relative motion")
    kinematic_viscosity: FloatProperty(name="Kinematic Viscosity", default=0, min=0, max=100,
                                       description="Implicit physical viscosity in square metres per second")
    viscosity_iterations: IntProperty(name="Viscosity Iterations", default=64, min=1, max=256)
    viscosity_tolerance: FloatProperty(name="Viscosity Tolerance", default=0.001,
                                       min=0.000001, max=0.1, precision=6)
    boundary_viscosity: FloatProperty(name="Boundary Viscosity", default=1, min=0, max=10,
                                      description="Multiplier for viscous coupling to moving collider surfaces")
    surface_tension: FloatProperty(name="Surface Tension", default=0.1, min=0, max=10)
    pressure_speed_gain: FloatProperty(name="Pressure Speed Limit", default=40, min=0, max=100000,
                                       description="Most speed that pressure may add to a particle per second, in metres "
                                                   "per second squared. Stops droplets being shot out of squeezed gaps "
                                                   "at many times the flow speed; 0 disables the limit")
    gravity: FloatVectorProperty(name="Gravity", default=(0, 0, -9.81), size=3)
    bounds_min: FloatVectorProperty(name="Minimum", default=(-3, -2, -1), size=3,
                                   subtype="XYZ", description="World-space initial GPU region; expands automatically")
    bounds_max: FloatVectorProperty(name="Maximum", default=(3, 2, 4), size=3,
                                   subtype="XYZ", description="World-space initial GPU region; expands automatically")
    remove_strays: BoolProperty(name="Remove Escaped Liquid", default=True,
                                description="Remove liquid that has fallen or been thrown well clear of every collider, emitter and the region above; it cannot come back, and would otherwise be simulated for as long as it keeps falling")
    auto_budgets: BoolProperty(name="Automatic Limits", default=True,
                               description="Let particles, collider samples and surface cells grow to the solver's own limits (4 million particles, 4 million collider samples, 64 million surface cells). Memory is taken as the simulation grows, so nothing is reserved in advance. Turn off to set smaller budgets that stop a runaway setup early")
    max_particles: IntProperty(name="Particle Budget", default=100000, min=128, max=8000000)
    max_whitewater: IntProperty(name="Whitewater Budget", default=25000, min=0, max=1000000)
    max_boundary: IntProperty(name="Collider Sample Budget", default=200000, min=1000, max=4000000,
                              description="Collider surface samples accepted per frame; finer particles need more")
    whitewater: BoolProperty(name="Foam, Spray and Bubbles", default=True, update=_display_update)
    whitewater_generation: FloatProperty(name="Generation", default=1, min=0, max=10)
    whitewater_size: FloatProperty(name="Particle Size", default=2, min=0.1, max=20, update=_look_update,
                                   description="Rendered size of spray, foam and bubble particles. 1 is the size used before Aquaris 0.2.8; larger particles read as denser foam")
    whitewater_mist: FloatProperty(name="Mist", default=0, min=0, max=100, update=_look_update,
                                   description="Density of a soft scattering volume built from spray and foam, for falls and heavy spray. 0 disables it. Needs Light Paths > Volume bounces above zero in Cycles to render white")
    whitewater_mist_radius: FloatProperty(name="Mist Radius", default=4, min=1, max=20, update=_look_update,
                                          description="Reach of each whitewater particle's mist, in liquid particle radii")
    whitewater_energy_min: FloatProperty(name="Minimum Energy", default=0.5, min=0, max=10000,
                                         description="Kinetic energy per unit mass in square metres per square second")
    whitewater_energy_max: FloatProperty(name="Maximum Energy", default=10, min=0, max=10000)
    whitewater_lifetime_min: FloatProperty(name="Minimum Lifetime", default=1, min=0.01, max=60)
    whitewater_lifetime_max: FloatProperty(name="Maximum Lifetime", default=3, min=0.01, max=60)
    whitewater_buoyancy: FloatProperty(name="Bubble Buoyancy", default=4, min=-100, max=100,
                                      description="Bubble acceleration in metres per second squared")
    whitewater_drag: FloatProperty(name="Drag", default=0.4, min=0, max=1)
    mesh_cell_relative: BoolProperty(name="Follow Particle Radius", default=True,
                                     description="Size the surface cells from the particle radius, so the surface keeps its level of detail when the resolution changes; restart to change")
    mesh_cell_factor: FloatProperty(name="Surface Cell", default=1.0, min=0.5, max=4.0, precision=2,
                                    description="Surface cell size in particle radii. 1 resolves every particle. With Fast quality 2 meshes about three times faster and looks softer; Final and Smooth refine the surface back to one radius, so a larger cell does not speed them up. Restart to change")
    mesh_cell_size: FloatProperty(name="Surface Cell Size", default=0.04, min=0.005,
                                 subtype="DISTANCE", description="Smaller cells give a finer surface; restart to change")
    max_mesh_cells: IntProperty(name="Surface Cell Budget", default=2000000,
                                min=1000, max=64000000)
    # Numbered as before the GPU item existed (0.2.12 and earlier files); files
    # saved by 0.2.13 or 0.2.14 used the order GPU, Fast, Final, Smooth.
    mesh_quality: EnumProperty(name="Mesh Quality", items=[
        ("GPU_SMOOTH", "GPU Smooth (preview)", "Built on the graphics card in milliseconds with the stretched kernels of Smooth: resting liquid is flat, "
                                               "splashes stay connected sheets. Droplets are simple and Droplet Size does not apply; "
                                               "needs a surface cell of at least half a particle radius and falls back to Fast when a frame does not fit", 4),
        ("GPU", "GPU Fast (preview)", "Built on the graphics card in milliseconds on round kernels: shows every particle-scale bump. "
                                      "Droplets are simple and Droplet Size does not apply; falls back to Fast when a frame is too large for it", 3),
        ("FAST", "Fast", "Smooth surface on the requested grid", 0),
        ("FINAL", "Final", "Refine coarse surface cells to retain thin features; uses more memory and time", 1),
        ("SMOOTH", "Smooth", "Anisotropic reconstruction reduces particle-scale bumps; slower and can join narrow gaps", 2),
    ], default="FINAL")
    droplet_scale: FloatProperty(name="Droplet Size", default=0.5, min=0.05, max=1, subtype="FACTOR",
                                 description="Size of detached droplets of one to three particles in the surface mesh. "
                                             "1 keeps their particle volume; smaller values draw splashes as finer spray")
    surface_smoothing: FloatProperty(name="Surface Smoothing", default=0.4, min=0, max=1, subtype="FACTOR",
                                     description="Extra filtering of the surface mesh that removes particle-scale dimples "
                                                 "while restoring each body's volume; costs meshing time and softens fine detail")
    surface_velocity: BoolProperty(name="Surface Motion Vectors", default=True,
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
        ("PREVIEW", "GPU Surface", "Smooth GPU surface preview without CPU meshing; bake for renderable geometry"),
        ("SURFACE", "Surface", "Renderable liquid mesh and whitewater"),
        ("PARTICLES", "Particles", "Liquid particle diagnostic display"),
    ], default="PREVIEW", update=_display_update)
    preview_quality: EnumProperty(name="Preview Quality", items=[
        ("FAST", "Fast", "Reduced resolution surface for large viewports"),
        ("BALANCED", "Balanced", "Full resolution smooth liquid surface"),
        ("HIGH", "High", "Additional surface filtering at full resolution"),
    ], default="BALANCED")
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
    effector_kind: EnumProperty(name="Force", items=[
        ("FORCE", "Force", "Uniform acceleration within radius"),
        ("WIND", "Wind", "Directional acceleration along local Z"),
        ("VORTEX", "Vortex", "Swirl around local Z"),
        ("TURBULENCE", "Turbulence", "Spatially varying acceleration"),
    ], default="FORCE")
    force_vector: FloatVectorProperty(name="Acceleration", default=(0, 0, 1), size=3)
    force_strength: FloatProperty(name="Strength", default=1, min=-1000, max=1000)
    force_radius: FloatProperty(name="Radius", default=2, min=0.01, subtype="DISTANCE")


def register():
    bpy.utils.register_class(LiquidProperties)
    bpy.types.Object.fumaris_liquid = PointerProperty(type=LiquidProperties)
    bpy.types.Scene.fumaris_liquid_domain = PointerProperty(name="Liquid Domain", type=bpy.types.Object)


def unregister():
    del bpy.types.Scene.fumaris_liquid_domain
    del bpy.types.Object.fumaris_liquid
    bpy.utils.unregister_class(LiquidProperties)
