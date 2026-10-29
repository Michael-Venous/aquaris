> **Aquaris 0.4.0-beta.1 — beta release.** Linux x64, Windows x64 and macOS arm64 / Blender 5.2+.
> This is prerelease software; keep backups of production scenes and caches.

# Aquaris documentation

Aquaris simulates liquid as particles moved by a pressure solve on a grid, in
its own Vulkan worker process. Its scene properties, simulation process and
caches are independent of the Fumaris smoke/fire addon, and both can be
installed together. This is an early release; platform and physical accuracy
claims depend on the validation record shipped with the build.

## What this version does and does not do

It simulates one liquid with mesh colliders, initial and continuous emitters,
outflows, gravity, viscosity, surface tension, force fields (effectors) and
whitewater (foam, spray and bubbles). It plays small scenes live, bakes larger
ones to a cache, and builds a renderable surface with motion vectors.

Versions up to 0.2.18 had a second, position-based particle solver. It was
removed on 2026-10-12; the grid solver is the only one, and viscosity, surface
tension, whitewater and force fields have since returned on it.

Scenes and caches from earlier versions still open. A scene that used the
particle solver now simulates on the grid solver and looks different; its
removed settings are ignored. A cache baked earlier plays back as it was
baked, including its whitewater (see Output, recording and rendering).

## Start a simulation

Open **Physics > Aquaris** or the viewport's **Aquaris** sidebar.
**Quick Setup** creates an isolated setup at the 3D cursor. The four
**real-time** setups (**Splash**, **Dam Break**, **Pour**, **Wave Tank**) have
15,000 to 26,000 particles and the GPU surface, sized to play back live with a surface
on a mid-range graphics card; Splash, a ball dropped into a pool, is the
default. **Dam Break (detailed)** and **Pour (detailed)** are the same two
scenes at a particle radius of 0.02 (about 50,000 particles) with the Smooth
mesh, and are meant to be baked. Existing scene objects stay in place. Each setup creates a domain
settings object, participant collections, a basin of five solids (the Wave
Tank has an open tray instead) and three output objects. **Add > Aquaris
Liquid** in the 3D viewport adds any setup at the 3D cursor without the dialog,
and **Quick Setup** is also offered when nothing is selected. Emitters appear as wireframes and are hidden from final rendering.

Select the domain or one of its participants, then **Play**. The selected
participant's settings appear first. Simulation, allocation, display
and cache controls are collapsible; no domain-object dropdown is required.
When a participant belongs to multiple simulations, select the desired domain.
Internally the extension keeps its original ID (`fumaris_liquid`), so scenes
and caches from earlier development versions load unchanged. **Pause** retains the entire running solver
state; **Resume** continues it. **Stop** terminates the worker. **Reset** clears
the displayed geometry; it does not delete recorded frames. One liquid session
can run at a time. Pausing completes the already submitted frame. Changing the
active scene or loading a different file stops the worker.

Domain minimum/maximum specify the initial GPU region. The native region grows
as needed. **Remove Escaped Liquid** (on by default, under Advanced) removes liquid that has fallen or been thrown more than a margin (one
metre, or a quarter of the scene's size if that is more) below or beside the
region and every collider and emitter: nothing is left for it to land on, and
it would otherwise be simulated for as long as it falls. The side gravity
points away from stays open, so liquid thrown upward comes back. Add collider meshes to make a physical container. Growing the region
uses additional GPU memory; a failed allocation stops with an error.

Particle radius sets the liquid sampling spacing (twice the radius). Smaller
values produce more particles and more work. Its slider spans 0.005 to 2 m;
any value from 0.1 mm to 100 m can be typed in. Surface cell size controls output
mesh detail independently. Radius, particle budgets, region and mesh sizing
should be chosen before starting; their UI is disabled while running. Gravity,
Substeps, Step Size, Collider Step, Simulation Speed and FLIP Ratio can be adjusted while playing.
**Substeps** (default 1) is the fewest steps the solver takes per frame; it adds more by
itself when the liquid or a collider moves fast, so 1 is enough for most
scenes. Gravity is entered in metres per second squared and is
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
particle radius, with their current surface velocity and the outward normal of
their face: the solid is behind each sample. Colliders must therefore be
meshes whose face normals point out of the solid towards the liquid (Blender's
default for boxes and spheres). The solver resolves a collider on its grid,
whose cell is two and a half particle radii, so this is not an exact triangle
collision representation. Thin sheets, sharp edges and rapidly moving geometry
need careful testing at the chosen radius.

A collider's **Margin** (Collider panel, default 0) adds thickness to its
surface. A closed mesh grows outward by the margin. An open mesh, such as a
plane or a sheet with open edges, becomes a slab the margin thick on each
side, so it holds liquid from both sides and the liquid rests the margin away
from it whichever way its faces point (without a margin, liquid on the back of
a plane rests about a grid cell and a half above it). A slab thinner than about
one grid cell is widened to one cell by the solver. An open mesh with a margin
sends twice the samples.

**Effectors** (force fields) push the liquid around an object: a uniform
force, wind along the object's Z, a vortex around it, or turbulence. Each
reaches out to its Radius, at full strength up to three quarters of it.

Outflows remove particles inside their evaluated **world-aligned bounding box**.
The runtime supports 32 outflows, five of which (six without
gravity) are taken by Remove Escaped Liquid while it is on. Collider surfaces are sampled
up to 8,000,000 samples; finer particle radii need more of them and more GPU
memory.

## The solver

The liquid is carried by particles two radii apart. Each step transfers their
velocity to a grid whose cell is two and a half particle radii, makes it
divergence-free there with a pressure solve, and moves the particles with the
result. The solver takes a few steps a frame however fast the liquid moves;
15,000 to 25,000 particles play back live on a mid-range graphics card, and
about 100,000 take roughly 100 ms a frame on an RTX 4060 Ti.

**FLIP Ratio** (Liquid section, default 0.95) sets how lively the liquid is. Each
step a particle takes that share of its new velocity from the change the grid
computed (FLIP: keeps detail and energy, but particle velocities get noisy)
and the rest from the grid's own smooth velocity (PIC: calm, looks thicker
and damped). 0.95 suits splashes; lower it for calm or slow liquid, raise it
towards 1 for the most energetic spray. It can be changed while playing.

The grid is a window fitted to the liquid before every step. There is no
fixed domain box and no wall at the edge of the region: liquid is held only by
collider meshes.

**Step Size** (Liquid section, default 3) is how far the fastest liquid may travel
in one solver step, in grid cells. At 3 the solver takes about half the steps
and is the right choice for previews, but how high a splash runs up a wall and
how the liquid settles then depend on Substeps (by about a third between 1 and
6 substeps). At 1 they no longer do (3%), and a dam break runs 2.1 column
heights up the far wall, as measured ones do, for about twice the time per
frame. **Draft** sets 3 and **Final** sets 1.

**Collider Step** (Liquid section, default 1.8) is how far a moving collider near
the liquid may travel in one solver step, in grid cells, never more than Step
Size. A fast collider sets the number of steps: a ball falling into a pool takes
about three steps a frame at 1.8 and five at 0.9. Lower it to 0.9 for the
cleanest impacts: at 1.5 and above the crown a ball throws up breaks into more
pieces (three times as many in the Splash setup). From 2.0 up, a collider
pressing liquid against a thin floor can push some through it.

The liquid keeps its volume: each step the particles are moved, without
changing their velocity, so that their density returns to what it was when
they were emitted. Liquid emitted with gaps to its container therefore closes
them, and liquid emitted overlapping a collider or packed too densely expands
to its proper volume in the first steps.

Where two streams meet in a corner, such as beside a block of liquid released
next to a wall, a thin jet goes up faster than free fall allows any single
drop elsewhere; single droplets of it can reach three or more times the
liquid's starting height. At Step Size 3 that jet is larger than at 1.

## Draft and Final

The **Draft** and **Final** buttons under the domain's status set the
settings that trade speed for quality in one step, and light up when the
current settings match. Draft: solver Step Size 3, a surface cell
of 1.5 radii and the **Smooth** mesh quality. Final: solver Step Size 1 and the
**Smooth** mesh quality at a surface cell of one radius. Every setting stays
editable afterwards. New domains and every Quick Setup start with the fast
preview settings (Smooth mesh, GPU Surface display, Step Size 3, Substeps 1).

## Mesh Quality

There are two surface qualities, both built on the graphics card in
milliseconds (about 25 ms for 200,000 particles on an RTX 4060 Ti):

- **Smooth** (default) closes and smooths the liquid's density field before
  meshing it: resting liquid is flat, splashes stay connected sheets, and slits
  and pores between particles fill. A particle lying on the surface makes a
  low swell, not a spike; one clear of it is a droplet. It joins bodies whose
  surfaces are within about three particle radii.
- **Detailed** meshes the raw density field: it keeps every particle-scale
  bump and ripple, and encloses less volume in broken-up liquid (16-20% less in
  a splashing dam break).

A frame too large for the graphics card is meshed on the processor instead,
with a warning. (Earlier versions also had processor-built qualities; files
that used them open with Smooth, or Detailed for the old Fast.)

The live GPU Surface preview always uses Smooth; the chosen quality applies to
the Surface display, recordings and bakes.

The surface settings (cell, Mesh Quality, Surface Smoothing,
motion vectors) can be changed during a run. They apply from the next frame;
while paused, the frame the solver is holding is meshed again at once with
the new settings, without advancing the simulation. They are fixed during a
bake.

## Limits and surface detail

Limits live in a collapsible **Limits** box under **Advanced**, closed and off
by default, as in Mantaflow and FLIP Fluids. **Automatic Limits** (on by default) lets a domain use up to the solver's own
limits: 4 million liquid particles, 8 million collider samples and 64 million
surface cells (a fixed Particle Budget may be set up to 8 million for
particle-only output). Nothing is reserved in advance; memory is taken as the
simulation grows, so lowering the particle radius simply works until the GPU
runs out, which is reported. Turn it off to use the **Particle Budget**,
**Collider Sample Budget** and **Surface Cell Budget** as early stops for a
runaway setup.

With **Follow Particle Radius** (on by default) the surface cell is
**Surface Cell** times the particle radius, so the surface keeps its level of
detail when the resolution changes. 1 resolves every particle; larger values
mesh faster and look softer. Turn it off to type an absolute
**Surface Cell Size**. Domains saved before 0.2.11 used an absolute size; they
now follow the radius at a factor of 1 unless this is turned off.

## Output, recording and rendering

The **Surface** object is a normal Blender mesh with an editable water material.
**Particles** is a diagnostic display with a velocity attribute.
Switching **Display** changes surface/particle visibility. All output objects
are tagged with their domain owner; changing another domain does not rewrite
them.

With **Generate Whitewater** on, foam, spray and bubbles are simulated and
written to the **Whitewater** output object; **Show Whitewater** switches them
on or off in the viewport and render, and the look settings change an existing
cache at once, without rebaking. The points carry `velocity`, an integer
`whitewater_type` (1 spray, 2 foam, 3 bubble), `whitewater_life` (seconds
left) and an integer `id` that stays with a particle from its birth to its
removal (for a random size or culling that does not flicker); an editable Geometry Nodes
graph turns them into a point cloud of small spheres, which Cycles blurs along
`velocity` when Motion Blur is on. An unedited instanced-sphere graph saved by
0.2.8 or earlier is converted in place the next time a frame is shown; an
edited one is left as it is. **Particle Size** scales the spheres; the default
of 2 reads as denser foam than the size used before 0.2.8 (set 1 for the old
look). **Mist** adds a soft white scattering volume built from spray and foam:
2-4 with **Mist Radius** 4 veils a waterfall without hiding it, while much
higher values turn into cotton-like blobs around single particles. Mist is a
separate **Mist** object that shares the whitewater points, is created the
first time Mist is above zero, appears in Surface display and in renders, and
needs Cycles **Light Paths > Volume** bounces above zero (8 or more) to render
white rather than grey.

Choose a cache directory and slot. Enable **Record Preview** before Play to save
every completed displayed frame. **Bake** runs the frame range in the Simulate
block. With **Use Timeline** (the clock button, on for new domains) that is the
scene's timeline, started **Warmup** frames before its first frame so the
liquid has settled and is moving when the timeline begins. With it off, the
bake runs its own Start to End; Quick Setup presets use 120 frames from the
current frame this way. During a bake the viewport shows a frame about ten
times a second; every frame is cached.
**Compress Cache** (on by default) writes frames compressed, about half the
disk space (a 115 MB beach frame becomes 59 MB) for a few tens of milliseconds
to write and read each frame; caches written either way, or by earlier
versions, are read. **Cache Particles** (on by default) keeps the liquid's
particles in each frame. They are needed for the Particles display, Rebuild
Surface and particle or volume export; turned off, a bake that only renders the
surface and whitewater takes about 40 % less space.
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
installed and its cache directory accessible. **Export Alembic** (Cache and Export)
writes the surface, particles or whitewater of a contiguous recorded frame
range for applications that do not load the addon, and **Export VDB** writes
one OpenVDB file per baked frame (the surface as a level set, with the liquid's
density and velocity) into a folder.

Render caches store mesh and particle output, not an exact resumable solver
state. Preview recording does not imply backward solver scrubbing. To start a
fresh simulation, stop and Play again; initial-fill sources are seeded again.

## Current fidelity and feature boundaries

Viscosity, surface tension, whitewater and force fields (effectors) are
simulated on the grid solver; see their sections above.

Known limits of the solver, measured by its validation suite
(`tools/tests/flip_validation_gpu_test.cpp` in the source archive): at Step
Size 3 the height reached on a wall depends on Substeps and corner jets are
too large; at 1 both are within a few percent of the small-step result.

Copy Diagnostics includes the current warning, device, Blender version and
serialized settings.

The liquid solver runs on the GPU, as do both mesh qualities (a frame too large
for the graphics card is meshed on the CPU instead, with a warning). Scene
sampling and the transfer of results to Blender run on Blender's main thread;
large scenes may pause the UI during sampling.
No fluid-driven rigid body, multi-liquid phase, ocean-spectrum tool or
viscoelastic material is exposed in this build. Volume input converts a single
isosurface; it does not import VDB velocity grids or multiple liquid phases.

The solver has been exercised on Linux with NVIDIA (RTX 3090 Ti, RTX 4060 Ti)
and Intel (Arc/ARL) GPUs: the Quick Setups run and keep their liquid in the
basin, bakes are written and play back, pause and resume hold and continue
the same state, and a cache with whitewater in the earlier format plays back
with its look controls. Windows 11 x64 on Intel Arc 140V and macOS 26.6.1 arm64 on Apple M4 were qualified on 2026-10-26 with actual installed Blender bake, preview, pause/resume, stop/delete, save/reload and export checks. macOS is ad-hoc signed, not notarized; Intel Macs and older macOS versions are untested. Windows non-ASCII install paths remain unqualified.
AMD GPUs have not been tested. Coarse sources can produce visibly layered inflow;
smaller particle radius and round source shapes give more detail at increased
cost.

The source qualification also writes actual small VDB files with Blender's
MeshToVolume modifier, fills two animated frames through native VolumeToMesh,
checks translated/rotated/scaled source transforms and cleans failed conversion
objects. It verifies procedural/image masks, unrealized mesh instances and
origins, and inherited instance velocity. No Python OpenVDB library is required.


## Splash detail and motion blur

**Stick to Colliders** (on by default) lets the liquid's surface meet the
colliders it rests against. The particles keep about a radius away from a
solid, so without it the surface stands off a glass, a floor or a tank wall by
that much and ripples with them: water in a glass then shows a thin, uneven
layer of air against the glass that refracts like crushed ice. With it, the
surface that faces a collider within 1.5 radii is moved onto the collider's
face and a quarter radius into it; the top of a thin film on a floor stays
where it is. It applies to the Surface display, recordings and bakes; the GPU
Surface preview shows the surface as simulated (the lookups would cost a fast
scene a fifth of its frame rate).

**Surface Smoothing** filters the finished surface mesh to remove the
particle-scale dimples that make calm liquid look hammered. It ranges from 0 to
4 (the CPU meshers stop at 1). Vertices move at most about (0.25 + 0.75 x
smoothing) particle radii, and less where the liquid behind them is thin, so
detached droplets and thin sheets keep their shape; a bump joined to the body
of the liquid is smoothed with it rather than left standing as a spike. New
domains and the Quick Setups start at 1.0; Small-Scale Water uses 3; 0 gives
the unfiltered reconstruction.

**Motion Blur Data** stores a velocity for every surface vertex of baked and
recorded frames (it has no visible effect in the viewport),
interpolated from nearby particles, as the mesh's `velocity` point attribute.
With **Render > Motion Blur** enabled Cycles then blurs the liquid along its
motion even though the mesh topology changes every frame. It adds about a third
to the cache size. Surfaces rebuilt later from a stopped preview carry no
vectors. While a frame renders, its own cached liquid stays in place at the
shutter's open and close times; the blur comes only from these vectors. Between
frames (a subframe set by hand, or a scene rendered through another scene's
compositor) the nearer cached frame is shown.

## The panel

The domain's panel (Properties > Physics, and the Aquaris tab of the 3D view's
sidebar) starts with the **Simulate** block, which is always open: the status
(with a bake's progress and time left, and afterwards how the last bake went),
Play, Bake and Reset, Draft and Final, Particle Radius, the frame range (Use
Timeline with Warmup, or Start and End) and Record Preview. Particle Radius is
shown in millimetres, switching to centimetres from 0.1 m and to metres from
1 m (it follows the scene's unit scale). Below it are Participant Collections,
Liquid, Whitewater, Surface and Display, Cache and Export, Advanced (Simulation
GPU, Preview Rate, region, walls and the Limits box) and Diagnostics (the GPU, the last frame's timings, how full the
particle budget is, and Copy Diagnostics).

In **Cache and Export**, **+** beside Cache Slot switches to the next unused
slot (main, main_001, main_002 …) so a new bake is kept beside the earlier
ones, and the lock **protects** the slot's bake: it is then neither deleted nor
replaced. Baking into a slot that already holds frames asks first whether to
replace them.

Blender's own timeline playback stops a live preview (a bake continues).

## Header controls, shortcuts and preferences

While the scene has an Aquaris domain, the 3D viewport's and the timeline's
headers show **Play / Pause** and **Stop** buttons (and an **Aquaris** menu
with the domain to control) that work whatever is selected. **Shift+Alt+L**
plays, pauses and resumes; **Shift+Alt+K** stops. Both can be changed in
Preferences > Add-ons > Aquaris, which also shows the add-on version and
whether the bundled solver worker was found for this computer. (Fumaris uses
other keys, so the two add-ons can be installed together.)

If a simulation reports that the worker is missing or not running, reinstall
the extension ZIP that matches your operating system and check that a current
Vulkan GPU driver is installed; **Copy Diagnostics** (Diagnostics section)
collects the details to send with a support request.

## Interactive performance

**Preview Rate** (under Advanced) sets how fast the live preview plays:
**Scene Frame Rate** (default) shows frames at the scene's frame rate, so the
liquid moves in real time and the graphics card rests between frames; **As Fast
As Possible** shows each frame as soon as it is simulated. Bakes always run at
full speed.

**Simulation GPU**, under the domain's Advanced section and in Preferences > Add-ons >
Aquaris, chooses the card for the next preview or bake. This is an add-on-wide
preference, shared by domains. Stop the current simulation before changing it.

Choose **Choose GPU**, click the refresh icon, then select a card from **Device**.
The list shows detected hardware Vulkan compute devices, with a short unique ID
to distinguish identical models. The choice is saved by the full device UUID in
Blender's preferences, so enumeration order changes cannot select another card.
Use Blender's usual preference saving/Auto-Save Preferences to retain it across
restarts. Refresh only discovers devices; it does not run the solver. If a saved
card is disconnected, the choice stays visible and starting reports an error;
choose another card or switch to Auto. It never silently starts on another card.

**Auto** (default) prefers a card Blender is not drawing on when there is another
of the same kind; with one card it uses that one. **Blender's GPU** selects the
drawing card. **Named** retains the previous manual name-substring/index override
for scripts and older preferences. Diagnostics reports the actual solver GPU and
full device UUID. The selected UUID also routes GPU surface meshing to that card;
the existing CPU surface fallback still applies when GPU meshing cannot be used.

An explicit `FUMARIS_LIQUID_GPU` environment override takes precedence over the
preference (the panel shows when it is active). It accepts `uuid:<Vulkan device
UUID>` as well as the previous name substring or enumeration index. This choice
does not change Blender's drawing or Cycles render device.

Use **Surface and Display > Display > GPU Surface** for fast iteration. It is
the default for new domains; existing saved display selections remain compatible.
It draws the meshed surface itself: the triangles the GPU mesher builds each
frame go straight to the viewport without becoming a Blender mesh, so an
unrecorded frame costs no geometry update. Tint and opacity follow how much
liquid each view ray crosses, measured on the mesh (thin sheets are pale and
clear, deep liquid blue), with a simple studio reflection and, for a cache
that has some, depth-tested whitewater. An unrecorded preview is always meshed
on the GPU, whatever Mesh Quality is chosen for the recorded surface. Scene
refraction and material graphs require **Surface** output. An unrecorded
preview is not final render geometry. Switching a stopped preview to **Particles**
restores diagnostic points; switching to **Surface** asynchronously meshes its
exact frozen particles without advancing the simulation (see Mesh Quality).
Choose the quality before starting a simulation, or before switching a stopped
GPU Surface preview to Surface. **Record Preview** and
**Bake** always save the full surface, even when GPU Surface is selected. The
recorded surface remains enabled for final rendering, and cached GPU previews
reopen without scrubbing.

Surface reconstruction preserves closed topology when recovering very small
contour cuts. Recovery can take an extra CPU pass. If scene coordinates cannot
represent the requested detail, recenter the scene or increase mesh cell size.

The preview follows domain visibility. Reset, file loading and unregistering
release its temporary buffers. The OpenGL and Vulkan viewport paths were tested
on NVIDIA under Linux. Windowed installed-extension preview drawing was also verified on Windows/Intel Arc and macOS/Apple M4 (Metal) on 2026-10-26. Other platform/GPU combinations remain untested.

Static collider samples are retained by the worker until geometry or velocity
changes (including collider removal). Animated/evaluated geometry is still
checked each frame. Box, sphere and cylinder fills and collider sampling use array
operations. Continuous sources emit at exact
subframe ticks with advected/staggered sampling to avoid pulse-shaped layers.
Preview polling no
longer imposes a fixed 20 ms wait; it redraws on results rather than every poll.

For an unsaved `.blend`, relative simulation cache paths use a persistent per-user Aquaris cache folder instead of Blender's working directory. Saved files initially resolve `//` beside the `.blend`. The first recording pins the chosen location to an absolute path so Save As keeps the existing bake accessible. To move a project and its cache together, copy both and update Cache Directory to the copied folder.

A private per-user shader cache speeds repeated Play/Stop cycles (on Linux
`~/.cache/aquaris/shaders`, on Windows `%LOCALAPPDATA%\Aquaris\shaders`, on
macOS `~/Library/Caches/Aquaris/shaders`). Keys include source, defines, shader
includes and cache/build version; exact keys and checksums are verified.
Missing/corrupt entries are recompiled, and deleting the folder simply makes
the next start cold. The cache is optional and never stores simulation state.


## Small-scale droplets (0.4.0 beta)

Quick Setup > Droplets creates millimetre-size beads on an inclined plate with
the Small-Scale Water settings (surface tension, contact angle and grip; no
artistic adhesion or friction). It uses finer
resolution than the large Splash/Dam/Pour presets; smaller particles cost more.
The setup respects the scene's unit scale and does not change the scene units.

Surface Adhesion attracts liquid only within two solver grid cells of a collider.
Contact Friction resists sliding relative to that collider, limited by normal
contact and adhesion impulses. Both can change while playing. Existing scenes
keep zero adhesion and zero friction until you enable them. Try the Droplets
setup as a starting point, then adjust for your surface. Large impacts can detach
liquid; this is an artistic wetting model, not a calibrated material contact angle.
High adhesion or surface tension needs smaller timesteps and can slow playback.
Use enough particles to resolve a droplet; a handful of particles cannot represent
a detailed liquid interface. Whitewater bubbles are separate from liquid droplets.

**Liquid Preset** (previously Viscosity Preset) sets the liquid's material in
one step: Water, Small-Scale Water, Oil, Honey, Chocolate and Thick. Water is
the large-scale default (no surface tension, FLIP 0.95). **Small-Scale Water**
is for drops of a few millimetres: Surface Tension 0.5, Contact Angle 110,
Contact Grip 0.3, FLIP ratio 0.3 (calms the restless jitter of a resting drop),
no artistic adhesion or friction, Surface Smoothing 3 and Surface Cell 1.5.
A practical starting point is a particle radius of 0.1-0.15 mm, so a drop is at
least a dozen particles across; 0.1 mm is rounder and takes about twice as long.

Beneath Surface Tension, active while it is above 0:
**Contact Angle** (0-180 degrees) is how water meets a collider: below 90 it
wets and spreads, above 90 it beads (a leaf). **Contact Grip** (0-1) holds the
edge of a drop where it touches a collider, so small drops hang on a window
while heavier ones slide. While surface tension is on, particles that drift
loose from the surface of a small body are drawn back onto it, so a resting drop
does not grow beads and spikes; this costs a few percent and is off whenever
surface tension is 0, so large scenes are unchanged.

Mesh Contact (previously labelled Stick to Colliders) only makes the final liquid
surface meet nearby collider geometry. It does not hold liquid particles in place.
Use the physical adhesion/friction controls for that purpose.

Linux and macOS extension ZIP extraction can lose executable file mode bits.
Aquaris restores owner execution on its own bundled worker automatically. A
read-only installation or a drive mounted noexec can still prevent execution;
install into your writable user extension directory on a drive that permits it.
