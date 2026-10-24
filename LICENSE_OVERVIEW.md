# Aquaris License Overview

Aquaris is distributed as several distinct components. No single license
applies to the entire download.

| Component | License |
| --- | --- |
| Blender Python integration (`*.py`) | GNU GPL v3.0 or later (`LICENSE`) |
| Original Aquaris native worker (`bin/fumaris_liquid_bridge`) and its compute shaders (`bin/flip_shaders`, `bin/gpu_mesh_shaders`) | Aquaris Commercial Runtime License (`COMMERCIAL_RUNTIME_LICENSE.txt`) |
| Bundled third-party libraries | Their respective licenses (`THIRD_PARTY_NOTICES.md` and `licenses/`) |

**Aquaris requires its native worker.** The Blender integration cannot
simulate anything by itself: it launches the worker, a separate executable
included in this package, and exchanges data with it over a local pipe. The
worker is a binary; its source code is not part of this download.

The worker is a separate program started by the Blender integration. Its
commercial license applies only to the original Aquaris worker code. It does
not replace, restrict, or relicense GPL-covered Python code or third-party
components.

The complete corresponding source for the GPL Blender integration matching
this release is available at:

https://github.com/Michael-Venous/aquaris

You may use Aquaris to create personal and commercial artwork. Licenses govern
the software itself, not ordinary rendered images, animations, meshes, caches
or other output created with it.

Questions about which terms apply should be directed to the support contact on
the purchase receipt. This overview is explanatory; the applicable license
texts control if there is a conflict.
