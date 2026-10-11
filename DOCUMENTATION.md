# Aquaris 0.1.3 development build

Aquaris runs the MIT-licensed Nebula liquid solver in a separate Vulkan
worker. Its scene properties, simulation process and caches are independent of
the Fumaris smoke/fire addon. This is an early implementation; platform and
physical accuracy claims depend on the validation record shipped with the build.

## Start a simulation

Open **Physics > Aquaris** or the viewport's **Aquaris** sidebar.
Choose **Dam Break**, **Pour**, or **Viscous** to create an isolated setup at the
3D cursor. Existing scene objects stay in place. Each setup creates a domain
settings object, participant collections, a five-solid basin and three output
objects. Emitters appear as wireframes and are hidden from final rendering.

Select the domain or one of its participants, then **Play**. The selected
participant's settings appear first. Simulation, whitewater, allocation, display
and cache controls are collapsible; no domain-object dropdown is required.
When a participant belongs to multiple simulations, select the desired domain.
The working product name is Aquaris; existing extension IDs and caches retain
their original identity. **Pause** retains the entire running solver
state; **Resume** continues it. **Stop** terminates the worker. **Reset** clears
the displayed geometry; it does not delete recorded frames. One liquid session
can run at a time. Pausing completes the already submitted frame. Changing the
active scene or loading a different file stops the worker.

Domain minimum/maximum specify the initial GPU region. The native region grows
as needed. Add collider meshes to make a physical container. Growing the region
uses additional GPU memory; a failed allocation stops with an error.

Particle radius sets the liquid sampling spacing (twice the radius). Smaller
values produce more particles and more work. Surface cell size controls output
mesh detail independently. Radius, particle budgets, region and mesh sizing
should be chosen before starting; their UI is disabled while running. Gravity,
pressure iterations, substeps, speed, viscosity, tension and whitewater can be
adjusted while playing. Gravity is entered in metres per second squared and is
converted through the scene's unit scale; positions/radii follow Blender units.

## Participants

Assign an object's **Liquid Role** and link it into the corresponding collection
on the domain. Nested collections are traversed and duplicate links are ignored.
The animated **Enabled** property is evaluated on every simulated frame.

Emitters support these sources:

- **Closed Mesh** fills the interior of evaluated, watertight triangles. Concave
  volumes and cavities use parity testing. Open/nonmanifold emission meshes are
  rejected with an actionable error.
- **Box/Sphere** use the evaluated object's transformed local bounds. Sphere
  fills an ellipsoid within those bounds.
- **Geometry Nodes / Vertices** uses evaluated `position` attributes or mesh
  vertices. If only unrealized instances exist, their world-space origins supply
  points. GN point components must be exposed as evaluated positions, mesh
  vertices, a Point Cloud, or instance origins.
- **Particle System** uses alive Blender particles and their velocities.
- **Collection Meshes** fills closed, evaluated meshes from a nested source
  collection using the owning emitter's controls.
- **Geometry Nodes Instances** fills the closed evaluated mesh prototype of each
  unrealized instance, using its complete instance transform. Instance motion is
  tracked by Blender's persistent instance IDs. Closed Mesh mode also falls back
  to these prototypes when the original instancer has no direct mesh. Native
  prototypes can supply their evaluated POINT attributes; arbitrary named
  attributes stored on the GN instance domain are not propagated or inferred.
- **Volume / OpenVDB** uses a Blender Volume object, including loaded VDB files,
  animated VDB sequences and evaluated Mesh to Volume modifiers. Choose the
  grid (usually `density`), threshold and conversion resolution. Blender's
  native VolumeToMesh creates a temporary closed surface that is then filled.
  Use threshold zero for a signed-distance surface grid. Voxel Size resamples
  the field; Original Grid preserves its native voxel resolution. Temporary
  conversion objects and meshes are removed on success and failure. GN volume
  components on a mesh object must be converted with a Volume to Mesh node in
  the GN graph; this build does not extract those anonymous volume components.

**Initial Fill** emits once on the first enabled frame that has samples.
**Continuous Inflow** emits at a frequency measured in ticks per simulation
second. Each tick samples the source volume. Native overlap filtering prevents
repeatedly adding a reservoir's occupied samples. Particle budgets are enforced;
the exporter does not silently discard samples to fit a budget.

Samples inside closed colliders or within two particle radii of their surfaces
are excluded before emission. Open collider meshes exclude only nearby samples.
The frame status reports this exclusion count. Keep the source volume clear of
solids for its full intended flow; collider animation through existing water is
handled by sampled contacts.

Initial velocity is world-space. Inherited motion uses evaluated transform or
deformation changes between frames. Topology changes fall back to transform
motion. Normal velocity uses the nearest evaluated surface normal. A POINT
float **Mask Attribute** applies a threshold; closed-volume samples use the
nearest evaluated vertex's mask. POINT vector **Velocity Attribute** contributes
an initial velocity transformed from object space. These are explicit evaluated
attributes read directly from evaluated geometry.

**Texture Mask** additionally supports native procedural Blender Texture
datablocks, static FILE/GENERATED images and numbered image sequences. The
attribute and texture masks must each pass Mask Threshold. Procedural textures
use Blender's scalar intensity; image masks use bilinear RGB luminance multiplied
by alpha. For an image whose Source is Image Sequence, each simulation frame
reads the file numbered scene frame plus **Sequence Offset** from the folder of
the image's file; frames before the first or after the last file hold that file,
and a missing number inside the range stops the run with the file name. The
image datablock's own frame, start and cyclic settings are not used. Movies,
UDIM tiles and node-based legacy Texture graphs are rejected explicitly.

Source Bounds maps each evaluated geometry's local bounds to 0–1. Procedural
textures then receive the native -1–1 coordinates; image projection uses 0–1 UV.
Object uses source-local coordinates and World uses world coordinates. Mask
Scale and Offset apply after the chosen mapping. Images project through XY, XZ
or YZ, with Blender's bottom-to-top pixel convention. Samples outside the image's
0–1 UV rectangle are disabled. For instances and collection members, bounds and
object coordinates refer to each evaluated geometry, while world coordinates
remain shared across sources. Texture masks sample actual liquid positions,
including volume and point/particle sources. Vertex groups are not sampled.

Colliders use evaluated mesh surfaces, including modifiers and deforming
animation. Triangles receive approximately isotropic samples spaced at one
particle radius, with their current surface velocity. This sampled boundary has
an effective contact offset of about two particle radii and is not an exact
triangle collision representation. Thin sheets, sharp edges, rapidly moving
geometry and dense boundaries need careful testing at the chosen radius.

Effectors support localized force, local-Z wind, local-Z vortex and turbulence.
Outflows remove particles inside their evaluated **world-aligned bounding box**.
The runtime supports 31 effectors and 32 outflows. Collider surfaces are sampled
up to 4,000,000 samples; finer particle radii need more of them and more GPU
memory.

## Draft and Final

The **Draft** and **Final** buttons under the domain's status set the settings
that trade speed for quality in one step, and light up when the current
settings match. Draft: 2 pressure iterations, Fast mesh, surface cell of 2
particle radii; the solve takes about half as long and meshing several times
less, splashes are softer and the surface coarser. Final: 6 iterations, Smooth
mesh, cell of 1 radius. Every setting stays editable afterwards.

## Limits and surface detail

**Automatic Limits** (on by default) lets a domain use up to the solver's own
limits: 8 million liquid particles, 4 million collider samples and 64 million
surface cells. Nothing is reserved in advance; memory is taken as the
simulation grows, so lowering the particle radius simply works until the GPU
runs out, which is reported. Turn it off to use the **Particle Budget**,
**Collider Sample Budget** and **Surface Cell Budget** as early stops for a
runaway setup. The Whitewater Budget is always fixed, because its memory is
reserved when the solver starts.

With **Follow Particle Radius** (on by default) the surface cell is
**Surface Cell** times the particle radius, so the surface keeps its level of
detail when the resolution changes. 1 resolves every particle. With Fast
quality 2 meshes about three times faster and looks softer; Final and Smooth
refine the surface back to one radius, so a larger cell does not speed them up. Turn it off to type an absolute
**Surface Cell Size**. Domains saved before 0.2.11 used an absolute size; they
now follow the radius at a factor of 1 unless this is turned off.

## Output, recording and rendering

The **Surface** object is a normal Blender mesh with an editable water material.
**Whitewater** stores `velocity` and integer `whitewater_type` attributes:
1 spray, 2 foam, 3 bubble. Its editable Geometry Nodes graph turns them into a
point cloud of small spheres, which Cycles blurs along `velocity` when Motion
Blur is on, so spray in a fast fall streaks with the water. An unedited
instanced-sphere graph saved by 0.2.8 or earlier is converted in place the next
time a frame is shown; an edited one is left as it is. **Particle Size** scales
the spheres; the default of 2 reads as
denser foam than the size used before 0.2.8 (set 1 for the old look).
**Mist** adds a soft white scattering volume built from spray and foam, for
falls and heavy spray: 2-4 with **Mist Radius** 4 veils a waterfall without
hiding it, while much higher values turn into cotton-like blobs around single
particles. Mist is a separate **Mist** object that shares the whitewater
points, is created the first time Mist is above zero, appears in Surface
display and in renders, and needs Cycles **Light Paths > Volume** bounces above
zero (8 or more) to render white rather than grey. Both settings change the
look of an existing bake at once. **Particles** is a diagnostic display with a
velocity attribute.
Switching **Display** changes surface/particle visibility. Whitewater remains
independently controlled. All output objects are tagged with their domain owner;
changing another domain does not rewrite them.

Choose a cache directory and slot. Enable **Record Preview** before Play to save
every completed displayed frame. **Bake** runs the chosen start/end frame range.
A run requires an empty slot so old frames cannot contaminate a new simulation.
Choose a new slot or use **Delete Slot** to remove the current domain's owned
slot. A writer lock prevents simultaneous writes; a cancelled run retains only
completed frames. Cache frames are versioned and written atomically.

Normal Stop/Cancel releases the writer lock. If Blender crashes or is killed
during a run, its lock stays in the slot. On Linux the next **Delete Slot**, Bake
or Record Preview on that slot removes the lock automatically once the process
that wrote it no longer exists on this computer. Completed frames in the slot
remain available for playback; because a run needs an empty slot, use **Delete
Slot** (or choose a new slot) before simulating there again. The lock is kept,
and the slot stays locked, whenever that cannot be established: a lock written
by another computer or from inside a different sandbox (for example a Flatpak
Blender), on a network or other non-local filesystem, by an Aquaris version
before 0.2.2, or on Windows and macOS. In those
cases choose a fresh slot, or remove `.writer.lock` from the slot folder yourself
after confirming no simulation is using it.

Enable **Timeline Cache Playback**, scrub frames, or reopen the scene to load
the chosen slot. Missing frames clear the output so a render cannot use stale
geometry. Playback also runs during background rendering. Keep the extension
installed and its cache directory accessible. **Export Surface Alembic** exports
a contiguous recorded frame range for applications that do not load the addon.

Render caches store mesh and particle output, not an exact resumable solver
state. Preview recording does not imply backward solver scrubbing. To start a
fresh simulation, stop and Play again; initial-fill sources are seeded again.

## Current fidelity and feature boundaries

**Pressure Speed Limit** (Simulation, default 40 m/s²) bounds how fast the
pressure solve may push a particle beyond the motion around it. Pressure may
stop or turn a particle freely, and may speed it up to a speed that several of
its own neighbours already have, liquid or collider: that is momentum being handed on, as in
a splash crown, a jet striking a pool or liquid thrown by a paddle. Only speed
that nothing next to the particle has is limited to this rate. Without it,
liquid squeezed into a narrow gap — for example between a collapsing column and
a nearby wall — shot individual droplets out at several times the fastest
physically possible speed. The value is stated for Earth gravity and rises in
proportion under stronger gravity. Set it to 0 to disable the limit.

Nebula uses position-based fluids. **Kinematic Viscosity** is the implicit
viscosity coefficient in square metres per second; it is converted through the
scene's squared unit scale. **Velocity Smoothing** is separate bounded XSPH
smoothing (0–1). The viscosity iterations/tolerance control convergence and
boundary viscosity controls coupling to sampled moving solids. **Surface
Tension** is a solver tuning coefficient, rather than a calibrated SI material
property. Presets provide visual starting points. Volume conservation,
convergence and high-viscosity behavior require further qualification before
production guarantees.

The runtime exposes convergence diagnostics. If viscosity reaches its iteration
limit above tolerance, the panel shows a warning; raise the iteration limit or
reduce the timestep. Numerical failures stop the worker with an error. A warning
clears after a later converged frame. Copy Diagnostics includes the current
warning, device, Blender version and serialized settings.

The liquid solver and whitewater run on the GPU; the current surface
reconstruction and transfer to Blender run on the CPU. Scene sampling likewise
runs on Blender's main thread. Large scenes may pause the UI during sampling.
No fluid-driven rigid body, multi-liquid phase, ocean-spectrum tool or
viscoelastic material is exposed in this build. Volume input converts a single
isosurface; it does not import VDB velocity grids or multiple liquid phases.

Current Linux qualification includes a 40-frame closed-basin dam break with
whitewater, three 48-frame pours at kinematic viscosities 0/0.05/0.5, cache
reopening, Alembic export/import and paused live viscosity updates. The pours
converged below 0.001 residual on an RTX 3090 Ti. These coarse box-source tests
show the implemented behavior; they are not a calibrated fixed-flow experiment.
Reservoir overlap filtering can change accepted particle count with viscosity.
Coarse sources can produce visibly layered inflow; smaller particle radius and
round source shapes give more detail at increased cost.

The source qualification also writes actual small VDB files with Blender's
MeshToVolume modifier, fills two animated frames through native VolumeToMesh,
checks translated/rotated/scaled source transforms and cleans failed conversion
objects. It verifies procedural/image masks, unrealized mesh instances and
origins, and inherited instance velocity. No Python OpenVDB library is required.


## Splash detail and motion blur

**Droplet Size** (Surface and Display) scales detached droplets of roughly one
to three particles in the surface mesh. At 1 each keeps its particle's volume,
which makes fine splashes look like beads; lower values (0.3–0.5) draw them as
finer spray. New domains start at 0.5. The simulation is unaffected, and connected liquid is never scaled.

**Surface Smoothing** filters the finished surface mesh to remove the
particle-scale dimples that make calm or viscous liquid look hammered. Each
connected body's volume is restored afterwards and vertices move at most about
one and a half particle radii at full strength, so thin sheets soften rather than
disappear. New domains start at 0.4 and the Viscous preset at 0.9; 0 gives the
unfiltered reconstruction.

**Surface Motion Vectors** stores a velocity for every surface vertex,
interpolated from nearby particles, as the mesh's `velocity` point attribute.
With **Render > Motion Blur** enabled Cycles then blurs the liquid along its
motion even though the mesh topology changes every frame. It adds about a third
to the cache size. Surfaces rebuilt later from a stopped preview carry no
vectors. While a frame renders, its own cached liquid stays in place at the
shutter's open and close times; the blur comes only from these vectors. Between
frames (a subframe set by hand, or a scene rendered through another scene's
compositor) the nearer cached frame is shown.

## Interactive performance

Use **Surface and Display > Display > GPU Surface** for fast iteration. It is
the default for new domains; existing saved display selections remain compatible.
This viewport filters particle depth on the GPU and reconstructs smooth surface
normals, with thickness, water shading and depth-tested whitewater. It avoids CPU
meshing and Blender geometry updates on unrecorded frames. **Preview Quality**
adjusts resolution/filtering. Transparency and studio reflections are simplified;
scene refraction and final material graphs require **Surface** output. An unrecorded
preview is not final render geometry. Switching a stopped preview to **Particles**
restores diagnostic points; switching to **Surface** asynchronously meshes its
exact frozen particles without advancing the simulation. **Mesh Quality > Final**
refines coarse surface cells to retain thin features; **Fast** keeps the requested
grid. Final can cost more time and memory. Both preserve detached droplets and
smooth the surface while preserving component volume. **Smooth** uses bounded
anisotropic kernels and local density compensation to reduce particle-scale
bumps and produce more continuous surfaces. It costs more CPU time and can join
closely spaced sparse features; it does not change the simulation or correct
underdense fluid. Fast and Final remain available for finer gap preservation.
Choose the quality before starting a simulation, or before switching a stopped
GPU Surface preview to Surface. **Record Preview** and
**Bake** always save the full surface, even when GPU Surface is selected. Recorded
surface/whitewater remain enabled for final rendering, and cached GPU previews
reopen without scrubbing.

Surface reconstruction preserves closed topology when recovering very small
contour cuts. Recovery can take an extra CPU pass. If scene coordinates cannot
represent the requested detail, recenter the scene or increase mesh cell size.

The preview follows domain visibility. Reset, file loading and unregistering
release its temporary buffers. The OpenGL and Vulkan viewport paths were tested
on NVIDIA under Linux. This does not qualify Metal, macOS or Windows.

Static collider samples are retained by the worker until geometry or velocity
changes (including collider removal). Animated/evaluated geometry is still
checked each frame. Box, sphere and cylinder fills and collider sampling use array
operations. Saved solver settings remain compatible; new presets use finer
particles and stronger pressure convergence. Continuous sources emit at exact
subframe ticks with advected/staggered sampling to avoid pulse-shaped layers.
Whitewater uses normalized local motion; still water does not create a spray cloud.
Preview polling no
longer imposes a fixed 20 ms wait; it redraws on results rather than every poll.

A private per-user temporary shader cache speeds repeated Play/Stop cycles.
Keys include source, defines, shader includes and cache/build version; exact
keys and checksums are verified. Missing/corrupt entries are recompiled. Clearing
the operating system's temporary directory simply makes the next start cold.
The cache is optional and never stores simulation state.
