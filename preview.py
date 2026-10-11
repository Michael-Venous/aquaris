"""Depth-correct GPU sphere impostors for inexpensive liquid iteration.

Temporary viewport output, never a substitute for the recorded surface. GPU
resources are created lazily in a draw context and released on file changes.
"""
import bpy
import numpy as np

_frames = {}
_handler = None
_shader = None
_targets = {}
_billboard = None


def shader():
    global _shader
    if _shader is None:
        import gpu
        interface = gpu.types.GPUStageInterfaceInfo('liquid_preview_interface')
        interface.smooth('VEC2', 'uv')
        interface.flat('VEC3', 'centerView')
        info = gpu.types.GPUShaderCreateInfo()
        info.vertex_in(0, 'VEC2', 'corner')
        info.sampler(4, 'FLOAT_2D', 'centersImage')
        info.push_constant('INT', 'centerWidth')
        info.vertex_out(interface)
        info.typedef_source('struct PreviewData { mat4 viewMatrix; mat4 projectionMatrix; vec4 appearance; };')
        info.uniform_buf(0, 'PreviewData', 'params')
        info.fragment_out(0, 'VEC4', 'fragColor')
        info.depth_write('ANY')
        info.vertex_source('''
        void main() {
            vec3 center = texelFetch(centersImage,ivec2(gl_InstanceID%centerWidth,gl_InstanceID/centerWidth),0).xyz;
            centerView = (params.viewMatrix * vec4(center, 1.0)).xyz;
            uv = corner;
            gl_Position = params.projectionMatrix * vec4(centerView + vec3(corner * params.appearance.w, 0.0), 1.0);
        }''')
        info.fragment_source('''
        void main() {
            float r2 = dot(uv, uv);
            if (r2 >= 1.0) discard;
            vec3 normal = vec3(uv, sqrt(1.0-r2));
            vec3 point = centerView + normal * params.appearance.w;
            vec4 clip = params.projectionMatrix * vec4(point, 1.0);
            gl_FragDepth = (clip.z / clip.w) * 0.5 + 0.5;
            vec3 light = normalize(vec3(-0.4, 0.6, 0.8));
            vec3 eye = params.projectionMatrix[3][3] == 0.0 ? normalize(-point) : vec3(0.0,0.0,1.0);
            float diffuse = max(dot(normal, light), 0.0);
            float specular = pow(max(dot(normal, normalize(light+eye)), 0.0), 72.0);
            float fresnel = 0.02 + 0.98 * pow(1.0-max(dot(normal, eye),0.0), 5.0);
            vec3 color = params.appearance.xyz*(0.35+0.65*diffuse) + vec3(0.55,0.72,0.85)*fresnel + specular*0.8;
            fragColor = vec4(color, 1.0);
        }''')
        _shader = gpu.shader.create_from_info(info)
    return _shader


class ParticleBatch:
    """One quad and one float texture, rather than duplicating particle vertices."""
    def __init__(self, positions):
        import gpu
        self.count=len(positions)
        self.width=min(1024,max(1,self.count))
        height=max(1,(self.count+self.width-1)//self.width)
        values=np.zeros((height*self.width,4),dtype=np.float32)
        values[:self.count,:3]=positions
        data=gpu.types.Buffer('FLOAT',values.size,values.ravel())
        self.texture=gpu.types.GPUTexture((self.width,height),format='RGBA32F',data=data)
        self.texture.filter_mode(False)

    def draw(self, program):
        global _billboard
        if not self.count:
            return
        if _billboard is None:
            from gpu_extras.batch import batch_for_shader
            _billboard=batch_for_shader(shader(),'TRIS',
                {'corner':[(-1,-1),(1,-1),(1,1),(-1,1)]},indices=[(0,1,2),(0,2,3)])
        program.uniform_sampler('centersImage',self.texture)
        program.uniform_int('centerWidth',self.width)
        _billboard.draw_instanced(program,instance_count=self.count)


def batch(positions):
    return ParticleBatch(positions)


def update(domain, positions, whitewater, whitewater_types=None):
    # Keep owned data: the protocol buffer may disappear after apply returns.
    _frames[domain.as_pointer()] = [domain, np.array(positions, dtype=np.float32),
                                  np.array(whitewater, dtype=np.float32), None, None,
                                  np.array(whitewater_types if whitewater_types is not None else [], dtype=np.uint32)]


def draw_surface(liquid, radius, view, projection, size, quality='BALANCED', key='offscreen'):
    from .screen_surface import SurfaceTarget
    scale = 0.65 if quality == 'FAST' else 1.0
    dimensions = tuple(max(1, int(value*scale)) for value in size)
    target = _targets.get(key)
    if target is None or target.size != dimensions:
        target = _targets[key] = SurfaceTarget(*dimensions)
    if len(_targets) > 4:
        oldest = next(iter(_targets))
        if oldest != key:
            _targets.pop(oldest)
    target.draw(liquid, radius, view, projection, iterations=3 if quality == 'HIGH' else 2)


def draw_batches(liquid, foam, radius, view, projection):
    import gpu
    program = shader()
    old_depth, old_mask, old_blend = gpu.state.depth_test_get(), gpu.state.depth_mask_get(), gpu.state.blend_get()
    try:
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.depth_mask_set(True)
        gpu.state.blend_set('NONE')
        program.bind()
        for geometry, size, color in ((liquid, radius*1.35, (0.025,0.24,0.38)),
                                      (foam, radius*0.32, (0.87,0.94,1.0))):
            if geometry is not None:
                values = np.concatenate((np.asarray(view, dtype=np.float32).T.ravel(),
                                         np.asarray(projection, dtype=np.float32).T.ravel(),
                                         np.asarray((*color, size), dtype=np.float32)))
                uniform = gpu.types.GPUUniformBuf(values)
                program.uniform_block('params', uniform)
                geometry.draw(program)
    finally:
        gpu.state.depth_test_set(old_depth)
        gpu.state.depth_mask_set(old_mask)
        gpu.state.blend_set(old_blend)


def _draw():
    context = bpy.context
    if context.region_data is None or context.space_data.type != 'VIEW_3D':
        return
    for key, frame in list(_frames.items()):
        domain, positions, whitewater, liquid, foam, types = frame
        try:
            if domain.name not in context.scene.objects or domain.fumaris_liquid.display != 'PREVIEW':
                continue
            if not domain.visible_get(view_layer=context.view_layer):
                continue
            if context.space_data.local_view and not domain.local_view_get(context.space_data):
                continue
            if liquid is None and len(positions):
                frame[3] = liquid = batch(positions)
            if foam is None and len(whitewater):
                if len(types) == len(whitewater):
                    frame[4] = foam = [(batch(whitewater[types == kind]), factor)
                        for kind, factor in ((1, .10), (2, .18), (3, .11)) if np.any(types == kind)]
                else:
                    frame[4] = foam = [(batch(whitewater), .12)]
            p = domain.fumaris_liquid
            view, projection = context.region_data.view_matrix, context.region_data.window_matrix
            if liquid is not None:
                draw_surface(liquid, p.particle_radius, view, projection,
                             (context.region.width, context.region.height),
                             getattr(p, 'preview_quality', 'BALANCED'), context.region.as_pointer())
            if p.whitewater and foam:
                for geometry, factor in foam:
                    draw_batches(None, geometry, p.particle_radius*factor/.32, view, projection)
        except ReferenceError:
            _frames.pop(key, None)
        except Exception as exc:
            domain.fumaris_liquid.last_error = 'GPU preview failed; select Surface or Particles: ' + str(exc)
            _frames.pop(key, None)


def clear(domain=None):
    global _shader, _billboard
    if domain is None:
        _frames.clear()
        _shader = None
        _billboard = None
        _targets.clear()
        from . import screen_surface
        screen_surface.clear()
    else:
        _frames.pop(domain.as_pointer(), None)


def register():
    global _handler
    if not bpy.app.background and _handler is None:
        _handler = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')


def unregister():
    global _handler
    if _handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handler, 'WINDOW')
        _handler = None
    clear()
