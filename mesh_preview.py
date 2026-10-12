"""Viewport drawing of the meshed liquid surface, straight from the worker's arrays.

The triangles the worker's mesher produced are drawn with the gpu module, so a
preview costs no Blender mesh update. It is real geometry: nothing is rebuilt
from particles in screen space. Depth-dependent tint comes from the length of
liquid a view ray crosses, taken from the mesh itself: every back face adds
its eye depth and every front face subtracts its own, which for a closed
surface sums to the distance travelled inside it.
"""
import numpy as np

_programs = {}
_targets = {}


def vertex_normals(vertices, triangles):
    """Area-weighted vertex normals of an indexed triangle mesh (float32, unit length)."""
    vertices = np.asarray(vertices, dtype=np.float32).reshape(-1, 3)
    triangles = np.asarray(triangles).reshape(-1, 3)
    normals = np.zeros((len(vertices), 3), dtype=np.float64)
    if len(triangles):
        a, b, c = (vertices[triangles[:, corner]] for corner in range(3))
        faces = np.cross(b - a, c - a).astype(np.float64)
        for corner in range(3):
            index = triangles[:, corner]
            for axis in range(3):
                normals[:, axis] += np.bincount(index, weights=faces[:, axis], minlength=len(vertices))
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return (normals / np.maximum(length, 1e-30)).astype(np.float32)


def _program(kind):
    import gpu
    if kind in _programs:
        return _programs[kind]
    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source('''struct LiquidMeshData {
        mat4 view; mat4 projection;
        vec4 settings; vec4 tint; vec4 region;
    };''')
    info.uniform_buf(0, 'LiquidMeshData', 'params')
    info.vertex_in(0, 'VEC3', 'position')
    interface = gpu.types.GPUStageInterfaceInfo('aquaris_mesh_' + kind)
    interface.smooth('VEC3', 'viewPosition')
    if kind == 'thickness':
        info.vertex_source('''void main() {
            vec4 point = params.view * vec4(position, 1.0);
            viewPosition = point.xyz;
            gl_Position = params.projection * point;
        }''')
        info.fragment_out(0, 'FLOAT', 'result')
        # Distance from the eye along the view ray; back faces add, front faces subtract.
        info.fragment_source('''void main() {
            float travelled = params.projection[3][3] == 0.0 ? length(viewPosition) : -viewPosition.z;
            result = gl_FrontFacing ? -travelled : travelled;
        }''')
    else:
        info.vertex_in(1, 'VEC3', 'normal')
        interface.smooth('VEC3', 'viewNormal')
        info.sampler(1, 'FLOAT_2D', 'thicknessImage')
        info.vertex_source('''void main() {
            vec4 point = params.view * vec4(position, 1.0);
            viewPosition = point.xyz;
            viewNormal = mat3(params.view) * normal;
            gl_Position = params.projection * point;
        }''')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.fragment_source('''
        vec3 environment(vec3 ray) {
            float sky = smoothstep(-0.35, 0.8, ray.y);
            vec3 color = mix(vec3(0.055,0.07,0.09),vec3(0.55,0.68,0.8),sky);
            float panel = pow(max(dot(ray,normalize(vec3(-0.55,0.7,0.5))),0.0),28.0);
            float strip = exp(-pow((ray.y-0.32)*18.0,2.0))*smoothstep(-0.8,0.2,ray.x);
            return color + vec3(1.25,1.18,1.05)*panel + vec3(0.7)*strip;
        }
        void main() {
            vec3 normal = normalize(viewNormal);
            vec3 eye = params.projection[3][3] == 0.0 ? normalize(-viewPosition) : vec3(0.0,0.0,1.0);
            if (dot(normal, eye) < 0.0) normal = -normal;
            float facing = max(dot(normal, eye), 0.0);
            float fresnel = 0.02 + 0.98*pow(1.0-facing, 5.0);
            // Liquid crossed by this pixel's ray; a face cut by the near plane leaves the sum too large.
            float thickness = clamp(texture(thicknessImage, (gl_FragCoord.xy-params.region.xy)/params.settings.yz).r,
                                    params.settings.x, params.settings.w);
            // Water takes red first: thin liquid stays pale and clear, deep liquid turns blue and opaque.
            vec3 transmission = exp(-thickness*vec3(2.2,0.7,0.35));
            vec3 base = mix(params.tint.xyz, vec3(0.62,0.86,0.92), transmission);
            vec3 light = normalize(vec3(-0.4,0.6,0.8));
            float diffuse = 0.55 + 0.45*max(dot(normal, light), 0.0);
            vec3 color = mix(base*diffuse, environment(reflect(-eye, normal)), fresnel);
            float specular = pow(max(dot(normal, normalize(light+eye)), 0.0), 100.0);
            color += vec3(0.95,0.98,1.0)*specular*0.8;
            float opacity = clamp(0.18 + 0.78*(1.0-transmission.g) + fresnel, 0.18, 0.98);
            fragColor = vec4(color, opacity);
        }''')
    info.vertex_out(interface)
    program = gpu.shader.create_from_info(info)
    _programs[kind] = program
    return program


class MeshBatch:
    """GPU buffers for one frame's surface."""
    def __init__(self, vertices, triangles, normals=None):
        import gpu
        vertices = np.ascontiguousarray(vertices, dtype=np.float32).reshape(-1, 3)
        triangles = np.ascontiguousarray(triangles, dtype=np.int32).reshape(-1, 3)
        self.count = len(triangles)
        self.batch = None
        if not self.count:
            return
        if normals is None or len(normals) != len(vertices):
            normals = vertex_normals(vertices, triangles)
        layout = gpu.types.GPUVertFormat()
        layout.attr_add(id='position', comp_type='F32', len=3, fetch_mode='FLOAT')
        layout.attr_add(id='normal', comp_type='F32', len=3, fetch_mode='FLOAT')
        buffer = gpu.types.GPUVertBuf(format=layout, len=len(vertices))
        buffer.attr_fill(id='position', data=vertices)
        buffer.attr_fill(id='normal', data=np.ascontiguousarray(normals, dtype=np.float32))
        self.batch = gpu.types.GPUBatch(type='TRIS', buf=buffer, elem=gpu.types.GPUIndexBuf(type='TRIS', seq=triangles))

    def draw(self, program):
        if self.batch is not None:
            self.batch.draw(program)


class _Target:
    """One float texture the size of the region, for the liquid crossed per pixel."""
    def __init__(self, width, height):
        import gpu
        self.size = (width, height)
        self.texture = gpu.types.GPUTexture(self.size, format='R32F')
        self.texture.filter_mode(False)
        self.framebuffer = gpu.types.GPUFrameBuffer(color_slots=self.texture)


def draw(mesh, radius, view, projection, size, key='offscreen'):
    """Draw into the bound framebuffer, whose size is `size`, depth-tested against what is there."""
    import gpu
    if mesh is None or mesh.batch is None:
        return
    old_viewport = gpu.state.viewport_get()
    # The rectangle actually drawn into decides the size; `size` is what the caller expects it to be.
    size = (max(1, int(old_viewport[2] or size[0])), max(1, int(old_viewport[3] or size[1])))
    target = _targets.get(key)
    if target is None or target.size != size:
        target = _targets[key] = _Target(*size)
    if len(_targets) > 4:
        oldest = next(iter(_targets))
        if oldest != key:
            _targets.pop(oldest)
    values = np.concatenate((np.asarray(view, dtype=np.float32).T.ravel(),
                             np.asarray(projection, dtype=np.float32).T.ravel(),
                             # Least and greatest depth of liquid used for tint: one particle radius to eighty.
                             np.asarray((radius, size[0], size[1], radius * 80.0, .02, .16, .30, 1.0,
                                         old_viewport[0], old_viewport[1], 0.0, 0.0), dtype=np.float32)))
    uniform = gpu.types.GPUUniformBuf(values)
    old_depth, old_mask, old_blend = gpu.state.depth_test_get(), gpu.state.depth_mask_get(), gpu.state.blend_get()
    try:
        with target.framebuffer.bind():
            gpu.state.viewport_set(0, 0, *size)
            gpu.state.depth_test_set('NONE')
            gpu.state.depth_mask_set(False)
            gpu.state.face_culling_set('NONE')
            # Plain one-plus-one: the single-channel output carries no alpha to scale by.
            gpu.state.blend_set('ADDITIVE_PREMULT')
            target.framebuffer.clear(color=(0.0, 0.0, 0.0, 0.0))
            program = _program('thickness')
            program.bind()
            program.uniform_block('params', uniform)
            mesh.draw(program)
        gpu.state.viewport_set(*old_viewport)
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        gpu.state.face_culling_set('BACK')
        gpu.state.blend_set('ALPHA')
        program = _program('surface')
        program.bind()
        program.uniform_block('params', uniform)
        program.uniform_sampler('thicknessImage', target.texture)
        mesh.draw(program)
    finally:
        gpu.state.viewport_set(*old_viewport)
        gpu.state.face_culling_set('NONE')
        gpu.state.depth_test_set(old_depth)
        gpu.state.depth_mask_set(old_mask)
        gpu.state.blend_set(old_blend)


def clear():
    _programs.clear()
    _targets.clear()
