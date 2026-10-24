# Third-Party Notices

Aquaris includes or dynamically uses third-party software. Each component
remains subject to its own license; no Aquaris license overrides those terms.

The simulation, the GPU and CPU surface meshers and the solver's compute
shaders are Aquaris's own code. No third-party solver library is built into or
distributed with the worker.

The packaged worker may include or was built with:

| Component | Use | License |
| --- | --- | --- |
| shaderc | Compiles the solver's shaders at run time (Linux package bundles it) | Apache-2.0 |
| glslang (used by shaderc) | Shader front end | BSD-3-Clause, MIT, Apache-2.0 and GPL-3.0 with Bison exception (see `licenses/glslang-LICENSE.txt`) |
| SPIRV-Tools (used by shaderc) | SPIR-V optimizer | Apache-2.0 |
| SPIRV-Headers (built into glslang and SPIRV-Tools) | SPIR-V definitions | Khronos license (`licenses/glslang-LICENSE.txt`) |
| Vulkan-Headers | Build-time headers | Apache-2.0 or MIT |
| nlohmann/json | Build-time header-only library | MIT |
| GCC runtime libraries (when linked statically or bundled) | C++ runtime | GPL-3.0 with GCC Runtime Library Exception |

Project home pages:

- https://github.com/google/shaderc
- https://github.com/KhronosGroup/glslang
- https://github.com/KhronosGroup/SPIRV-Tools
- https://github.com/KhronosGroup/SPIRV-Headers
- https://github.com/KhronosGroup/Vulkan-Headers
- https://github.com/nlohmann/json

Applicable license texts are in the `licenses` directory. The system Vulkan
loader and GPU drivers are not part of this package; they remain system
components under their own licenses. The exact set of bundled libraries
differs by platform package.
