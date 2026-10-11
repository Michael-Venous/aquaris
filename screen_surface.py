"""Screen-space liquid surface, with no CPU meshing or framebuffer readback.

Particle depth is filtered in view space before reconstructing the surface
normal. The result writes real depth into Blender's viewport, so scene geometry
still occludes the preview. Recorded/renderable geometry remains independent.
"""
import numpy as np

_programs = {}
_quad = None


def _program(kind):
    import gpu
    if kind in _programs:
        return _programs[kind]
    info = gpu.types.GPUShaderCreateInfo()
    info.typedef_source('''struct FluidData {
        mat4 projection; mat4 inverseProjection;
        vec4 settings; vec4 tint;
    };''')
    info.uniform_buf(0, 'FluidData', 'params')
    interface = gpu.types.GPUStageInterfaceInfo('aquaris_surface_' + kind)
    if kind in {'depth', 'thickness'}:
        info.vertex_in(0, 'VEC2', 'corner')
        info.sampler(4, 'FLOAT_2D', 'centersImage')
        info.push_constant('INT', 'centerWidth')
        interface.smooth('VEC2', 'uv')
        interface.flat('VEC3', 'centerView')
        info.push_constant('MAT4', 'viewMatrix')
        info.vertex_source('''void main() {
            vec3 center = texelFetch(centersImage,ivec2(gl_InstanceID%centerWidth,gl_InstanceID/centerWidth),0).xyz;
            centerView = (viewMatrix * vec4(center, 1.0)).xyz;
            uv = corner;
            gl_Position = params.projection * vec4(centerView +
                vec3(corner * params.settings.x, 0.0), 1.0);
        }''')
        info.fragment_out(0, 'FLOAT', 'result')
        if kind == 'depth':
            info.depth_write('ANY')
            info.fragment_source('''void main() {
                float rr = dot(uv, uv);
                if (rr >= 1.0) discard;
                vec3 point = centerView + vec3(uv, sqrt(1.0-rr))*params.settings.x;
                if (point.z >= 0.0) discard;
                vec4 clip = params.projection * vec4(point, 1.0);
                gl_FragDepth = clip.z / clip.w * 0.5 + 0.5;
                result = -point.z;
            }''')
        else:
            info.fragment_source('''void main() {
                float rr = dot(uv, uv);
                if (rr >= 1.0) discard;
                result = 2.0*params.settings.x*sqrt(1.0-rr);
            }''')
    else:
        info.vertex_in(0, 'VEC2', 'position')
        interface.smooth('VEC2', 'uv')
        info.vertex_source('''void main() {
            uv = position*0.5+0.5;
            gl_Position = vec4(position, 0.0, 1.0);
        }''')
        info.sampler(0, 'FLOAT_2D', 'depthImage')
        if kind == 'filter':
            info.push_constant('VEC2', 'direction')
            info.fragment_out(0, 'FLOAT', 'result')
            info.fragment_source('''void main() {
                float centerDepth = texture(depthImage, uv).r;
                if (centerDepth <= 0.0) { result = 0.0; return; }
                // Keep the filter footprint in world units in both ortho and
                // perspective views. Bounded taps keep cost independent of count.
                bool perspective = params.projection[3][3] == 0.0;
                float pixels = params.settings.x*abs(params.projection[1][1])*
                    params.settings.z*0.5/(perspective ? centerDepth : 1.0);
                float footprint = clamp(pixels*1.8, 1.0, 24.0);
                vec2 stepUv = direction/params.settings.yz * footprint/8.0;
                float sum = 0.0, weights = 0.0;
                float sigmaDepth = params.settings.x*1.2;
                for (int i=-8; i<=8; ++i) {
                    vec2 sampleUv = uv + float(i)*stepUv;
                    if (any(lessThan(sampleUv, vec2(0.0))) ||
                        any(greaterThan(sampleUv, vec2(1.0)))) continue;
                    float depth = texture(depthImage, sampleUv).r;
                    if (depth <= 0.0) continue;
                    float difference = (depth-centerDepth)/sigmaDepth;
                    float weight = exp(-float(i*i)/32.0-difference*difference*0.5);
                    sum += depth*weight;
                    weights += weight;
                }
                result = sum/max(weights, 1e-8);
            }''')
        else:
            info.sampler(1, 'FLOAT_2D', 'thicknessImage')
            info.fragment_out(0, 'VEC4', 'fragColor')
            info.depth_write('ANY')
            info.fragment_source('''
            vec3 viewPoint(vec2 coord, float depth) {
                vec4 nearPoint = params.inverseProjection*vec4(coord*2.0-1.0, -1.0, 1.0);
                vec4 farPoint = params.inverseProjection*vec4(coord*2.0-1.0, 1.0, 1.0);
                vec3 a=nearPoint.xyz/nearPoint.w, b=farPoint.xyz/farPoint.w;
                return a + (b-a)*((-depth-a.z)/(b.z-a.z));
            }
            vec3 neighbour(vec2 coord, vec3 fallback) {
                float depth = texture(depthImage, clamp(coord, vec2(0.0),vec2(1.0))).r;
                return depth > 0.0 ? viewPoint(coord, depth) : fallback;
            }
            vec3 environment(vec3 ray) {
                float sky = smoothstep(-0.35, 0.8, ray.y);
                vec3 color = mix(vec3(0.055,0.07,0.09),vec3(0.55,0.68,0.8),sky);
                float panel = pow(max(dot(ray,normalize(vec3(-0.55,0.7,0.5))),0.0),28.0);
                float strip = exp(-pow((ray.y-0.32)*18.0,2.0))*
                              smoothstep(-0.8,0.2,ray.x);
                return color + vec3(1.25,1.18,1.05)*panel + vec3(0.7)*strip;
            }
            void main() {
                float depth=texture(depthImage,uv).r;
                if (depth <= 0.0) discard;
                vec3 point=viewPoint(uv,depth);
                vec2 pixel=1.0/params.settings.yz;
                vec3 forwardX=neighbour(uv+vec2(pixel.x,0.0),point)-point;
                vec3 backwardX=point-neighbour(uv-vec2(pixel.x,0.0),point);
                vec3 forwardY=neighbour(uv+vec2(0.0,pixel.y),point)-point;
                vec3 backwardY=point-neighbour(uv-vec2(0.0,pixel.y),point);
                vec3 dx=dot(forwardX,forwardX)<1e-16 ? backwardX :
                        dot(backwardX,backwardX)<1e-16 ? forwardX :
                        abs(forwardX.z)<abs(backwardX.z) ? forwardX : backwardX;
                vec3 dy=dot(forwardY,forwardY)<1e-16 ? backwardY :
                        dot(backwardY,backwardY)<1e-16 ? forwardY :
                        abs(forwardY.z)<abs(backwardY.z) ? forwardY : backwardY;
                vec3 normal=cross(dx,dy);
                normal=dot(normal,normal)>1e-16 ? normalize(normal) : vec3(0.0,0.0,1.0);
                if (normal.z<0.0) normal=-normal;
                vec3 eye=params.projection[3][3]==0.0 ? normalize(-point) : vec3(0.0,0.0,1.0);
                float facing=max(dot(normal,eye),0.0);
                float fresnel=0.02+0.98*pow(1.0-facing,5.0);
                float thickness=max(texture(thicknessImage,uv).r,params.settings.x);
                vec3 transmission=exp(-thickness*vec3(2.4,0.65,0.32));
                vec3 base=mix(params.tint.xyz,vec3(0.05,0.23,0.31),transmission);
                float diffuse=0.45+0.55*max(dot(normal,normalize(vec3(-0.4,0.6,0.8))),0.0);
                vec3 color=mix(base*diffuse,environment(reflect(-eye,normal)),fresnel);
                vec3 light=normalize(vec3(-0.4,0.6,0.8));
                float specular=pow(max(dot(normal,normalize(light+eye)),0.0),100.0);
                color+=vec3(0.95,0.98,1.0)*specular*0.8;
                float opacity=clamp(0.55+0.35*(1.0-exp(-thickness*1.5))+fresnel,0.55,0.98);
                fragColor=vec4(color,opacity);
                vec4 clip=params.projection*vec4(point,1.0);
                gl_FragDepth=clip.z/clip.w*0.5+0.5;
            }''')
    info.vertex_out(interface)
    program = gpu.shader.create_from_info(info)
    _programs[kind] = program
    return program


def _fullscreen():
    global _quad
    if _quad is None:
        from gpu_extras.batch import batch_for_shader
        _quad = batch_for_shader(_program('filter'), 'TRIS',
            {'position': [(-1,-1),(1,-1),(1,1),(-1,1)]},
            indices=[(0,1,2),(0,2,3)])
    return _quad


class SurfaceTarget:
    """Reusable viewport-sized textures; no resources created on simulation ticks."""
    def __init__(self, width, height):
        import gpu
        self.size = (width, height)
        self.depth = gpu.types.GPUTexture(self.size, format='DEPTH_COMPONENT32F')
        self.images = [gpu.types.GPUTexture(self.size, format='R32F') for _ in range(2)]
        self.thickness = gpu.types.GPUTexture(self.size, format='R16F')
        for texture in (*self.images, self.thickness):
            texture.filter_mode(False)
        self.depth_fb = gpu.types.GPUFrameBuffer(depth_slot=self.depth,color_slots=self.images[0])
        self.filter_fb = [gpu.types.GPUFrameBuffer(color_slots=t) for t in self.images]
        self.thickness_fb = gpu.types.GPUFrameBuffer(color_slots=self.thickness)

    def draw(self, geometry, radius, view, projection, iterations=2):
        import gpu
        width,height=self.size
        old_viewport = gpu.state.viewport_get()
        old_depth, old_mask, old_blend = gpu.state.depth_test_get(),gpu.state.depth_mask_get(),gpu.state.blend_get()
        values = np.concatenate((np.asarray(projection,dtype=np.float32).T.ravel(),
                                 np.asarray(projection.inverted(),dtype=np.float32).T.ravel(),
                                 np.asarray((radius*1.4,width,height,0.0, .025,.19,.28,1.0),dtype=np.float32)))
        uniform=gpu.types.GPUUniformBuf(values)
        try:
            with self.depth_fb.bind():
                gpu.state.viewport_set(0,0,width,height)
                gpu.state.depth_test_set('LESS_EQUAL')
                gpu.state.depth_mask_set(True)
                gpu.state.blend_set('NONE')
                self.depth_fb.clear(color=(0.0,0.0,0.0,0.0),depth=1.0)
                program=_program('depth');program.bind()
                program.uniform_block('params',uniform);program.uniform_float('viewMatrix',view)
                geometry.draw(program)
            with self.thickness_fb.bind():
                gpu.state.viewport_set(0,0,width,height)
                gpu.state.depth_test_set('NONE');gpu.state.depth_mask_set(False)
                gpu.state.blend_set('ADDITIVE')
                self.thickness_fb.clear(color=(0.0,0.0,0.0,0.0))
                program=_program('thickness');program.bind()
                program.uniform_block('params',uniform);program.uniform_float('viewMatrix',view)
                geometry.draw(program)
            program=_program('filter');quad=_fullscreen();source=0
            gpu.state.blend_set('NONE')
            for _ in range(iterations):
                for direction in ((1.0,0.0),(0.0,1.0)):
                    destination=1-source
                    with self.filter_fb[destination].bind():
                        gpu.state.viewport_set(0,0,width,height)
                        program.bind();program.uniform_block('params',uniform)
                        program.uniform_sampler('depthImage',self.images[source])
                        program.uniform_float('direction',direction)
                        quad.draw(program)
                    source=destination
            gpu.state.viewport_set(*old_viewport)
            gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(True)
            gpu.state.blend_set('ALPHA')
            program=_program('shade');program.bind();program.uniform_block('params',uniform)
            program.uniform_sampler('depthImage',self.images[source])
            program.uniform_sampler('thicknessImage',self.thickness)
            quad.draw(program)
        finally:
            gpu.state.viewport_set(*old_viewport)
            gpu.state.depth_test_set(old_depth);gpu.state.depth_mask_set(old_mask);gpu.state.blend_set(old_blend)


def clear():
    global _quad
    _programs.clear()
    _quad=None
