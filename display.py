"""Renderable output meshes and editable materials/Geometry Nodes assets."""

import uuid

import bpy
import numpy as np

_last_outputs = {}


def ensure_identity(domain):
    p = domain.fumaris_liquid
    collisions = [obj for obj in bpy.data.objects if obj != domain and hasattr(obj, "fumaris_liquid") and
                  obj.fumaris_liquid.role == "DOMAIN" and obj.fumaris_liquid.domain_id == p.domain_id]
    owner = p.output_collection.get("fumaris_liquid_domain") if p.output_collection is not None else None
    duplicated = bool(collisions) and (owner != domain if owner is not None else
                                      domain.name != min([domain.name] + [obj.name for obj in collisions]))
    if not p.domain_id or duplicated:
        p.domain_id = str(uuid.uuid4())
        p.output_collection = None
        p.surface_object = None
        p.whitewater_object = None
        p.particle_object = None
    return p.domain_id


def _material(name, color, roughness, transmission=0.0):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.diffuse_color = (*color, 1)
    shader = material.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*color, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Transmission Weight"].default_value = transmission
    shader.inputs["IOR"].default_value = 1.333
    return material


def _particle_nodes(obj, material, radius, whitewater=False):
    group = bpy.data.node_groups.new(obj.name + " Display", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = group.nodes, group.links
    source = nodes.new("NodeGroupInput")
    source.location = (-600, 100)
    points = nodes.new("GeometryNodeMeshToPoints")
    points.mode = "VERTICES"
    points.location = (-400, 100)
    points.inputs["Radius"].default_value = radius
    sphere = nodes.new("GeometryNodeMeshIcoSphere")
    sphere.location = (-400, -120)
    sphere.inputs["Radius"].default_value = radius
    sphere.inputs["Subdivisions"].default_value = 1
    instances = nodes.new("GeometryNodeInstanceOnPoints")
    instances.location = (-160, 100)
    set_material = nodes.new("GeometryNodeSetMaterial")
    set_material.location = (-160, -120)
    set_material.inputs["Material"].default_value = material
    output = nodes.new("NodeGroupOutput")
    output.location = (470, 100)
    links.new(source.outputs["Geometry"], points.inputs["Mesh"])
    links.new(points.outputs["Points"], instances.inputs["Points"])
    # Assign the material once to the prototype and retain instances. Realizing
    # thousands of spheres multiplied geometry and dependency-graph work.
    links.new(sphere.outputs["Mesh"], set_material.inputs["Geometry"])
    links.new(set_material.outputs["Geometry"], instances.inputs["Instance"])
    links.new(instances.outputs["Instances"], output.inputs["Geometry"])
    if whitewater:
        type_attr = nodes.new("GeometryNodeInputNamedAttribute")
        type_attr.data_type = "INT"
        type_attr.inputs["Name"].default_value = "whitewater_type"
        type_attr.label = "1 spray / 2 foam / 3 bubble"
        type_attr.location = (-600, -360)
        foam = nodes.new("ShaderNodeMath")
        foam.operation = "COMPARE"
        foam.inputs[1].default_value = 2
        foam.inputs[2].default_value = 0.1
        foam.location = (-400, -360)
        scale = nodes.new("ShaderNodeMath")
        scale.operation = "MULTIPLY_ADD"
        scale.inputs[1].default_value = 0.08
        scale.inputs[2].default_value = 0.10
        scale.location = (-160, -360)
        links.new(type_attr.outputs["Attribute"], foam.inputs[0])
        links.new(foam.outputs[0], scale.inputs[0])
        links.new(scale.outputs[0], instances.inputs["Scale"])
    modifier = obj.modifiers.new("Editable Particle Display", "NODES")
    modifier.node_group = group
    obj["fumaris_liquid_display_version"] = 2
    obj["fumaris_liquid_display_radius"] = radius


def _canonical_particle_graph(group, whitewater, legacy=False, scaled=True):
    """Recognize only our complete generated graph before changing saved nodes."""
    by_type = {}
    for node in group.nodes:
        by_type.setdefault(node.bl_idname, []).append(node)
    required = {"NodeGroupInput", "NodeGroupOutput", "GeometryNodeMeshToPoints",
                "GeometryNodeMeshIcoSphere", "GeometryNodeInstanceOnPoints", "GeometryNodeSetMaterial"}
    if legacy:
        required.add("GeometryNodeRealizeInstances")
    if whitewater:
        required.add("GeometryNodeInputNamedAttribute")
        if not legacy and scaled:
            required.add("ShaderNodeMath")
    if set(by_type) != required or any(len(nodes) != (2 if kind == "ShaderNodeMath" else 1)
                                       for kind, nodes in by_type.items()):
        return None
    nodes = {kind: values[0] for kind, values in by_type.items() if kind != "ShaderNodeMath"}
    source, output = nodes["NodeGroupInput"], nodes["NodeGroupOutput"]
    points, sphere = nodes["GeometryNodeMeshToPoints"], nodes["GeometryNodeMeshIcoSphere"]
    instances, material = nodes["GeometryNodeInstanceOnPoints"], nodes["GeometryNodeSetMaterial"]
    if points.mode != "VERTICES" or sphere.inputs["Subdivisions"].default_value != 1:
        return None
    expected = [(source.outputs["Geometry"], points.inputs["Mesh"]),
                (points.outputs["Points"], instances.inputs["Points"])]
    if legacy:
        realize = nodes["GeometryNodeRealizeInstances"]
        expected += [(sphere.outputs["Mesh"], instances.inputs["Instance"]),
                     (instances.outputs["Instances"], realize.inputs["Geometry"]),
                     (realize.outputs["Geometry"], material.inputs["Geometry"]),
                     (material.outputs["Geometry"], output.inputs["Geometry"])]
    else:
        expected += [(sphere.outputs["Mesh"], material.inputs["Geometry"]),
                     (material.outputs["Geometry"], instances.inputs["Instance"]),
                     (instances.outputs["Instances"], output.inputs["Geometry"])]
    if whitewater:
        attribute = nodes["GeometryNodeInputNamedAttribute"]
        if attribute.data_type != "INT" or attribute.inputs["Name"].default_value != "whitewater_type":
            return None
        if not legacy and scaled:
            math = by_type["ShaderNodeMath"]
            compare = next((node for node in math if node.operation == "COMPARE"), None)
            scale = next((node for node in math if node.operation == "MULTIPLY_ADD"), None)
            if compare is None or scale is None:
                return None
            expected += [(attribute.outputs["Attribute"], compare.inputs[0]),
                         (compare.outputs[0], scale.inputs[0]),
                         (scale.outputs[0], instances.inputs["Scale"])]
    actual = {(link.from_socket.as_pointer(), link.to_socket.as_pointer()) for link in group.links}
    if actual != {(first.as_pointer(), second.as_pointer()) for first, second in expected}:
        return None
    return nodes


def _refresh_particle_nodes(obj, radius, whitewater):
    modifier = next((value for value in obj.modifiers if value.type == "NODES" and
                     value.name == "Editable Particle Display" and value.node_group), None)
    # Removing or replacing this modifier is an intentional customization.
    # A shared graph must not change another object's appearance.
    if modifier is None or modifier.node_group.users > 1:
        return
    group = modifier.node_group
    nodes = _canonical_particle_graph(group, whitewater,
                                      legacy=obj.get("fumaris_liquid_display_version") != 2,
                                      scaled="fumaris_liquid_display_radius_factor" not in obj)
    if nodes is None:
        return
    points, sphere = nodes["GeometryNodeMeshToPoints"], nodes["GeometryNodeMeshIcoSphere"]
    current_radius = float(sphere.inputs["Radius"].default_value)
    if abs(float(points.inputs["Radius"].default_value) - current_radius) > 1e-7:
        return
    if obj.get("fumaris_liquid_display_version") != 2:
        # Rewire only the known v1 material/realization path. Preserve the
        # actual material, radii, modifier, node positions and other settings.
        instances, material = nodes["GeometryNodeInstanceOnPoints"], nodes["GeometryNodeSetMaterial"]
        output = nodes["NodeGroupOutput"]
        group.nodes.remove(nodes["GeometryNodeRealizeInstances"])
        group.links.new(sphere.outputs["Mesh"], material.inputs["Geometry"])
        group.links.new(material.outputs["Geometry"], instances.inputs["Instance"])
        group.links.new(instances.outputs["Instances"], output.inputs["Geometry"])
        obj["fumaris_liquid_display_version"] = 2
        # Old whitewater used a smaller prototype. Preserve that appearance
        # instead of replacing its radius with the new prototype radius.
        obj["fumaris_liquid_display_radius_factor"] = current_radius / max(radius, 1e-8)
        obj["fumaris_liquid_display_radius"] = current_radius
        return
    previous = float(obj.get("fumaris_liquid_display_radius", radius))
    if abs(current_radius - previous) > 1e-7:
        return  # The user edited this radius directly in Geometry Nodes.
    target = radius * float(obj.get("fumaris_liquid_display_radius_factor", 1.0))
    if abs(current_radius - target) > 1e-7:
        points.inputs["Radius"].default_value = target
        sphere.inputs["Radius"].default_value = target
    obj["fumaris_liquid_display_radius"] = target


def ensure_outputs(scene, domain):
    identity = ensure_identity(domain)
    p = domain.fumaris_liquid
    collection = p.output_collection
    if collection is None or collection.get("fumaris_liquid_owner") != identity:
        collection = bpy.data.collections.new(domain.name + " Output")
        scene.collection.children.link(collection)
        collection["fumaris_liquid_owner"] = identity
        collection["fumaris_liquid_domain"] = domain
        p.output_collection = collection
    for prop_name, label in (("surface_object", "Surface"), ("whitewater_object", "Whitewater"), ("particle_object", "Particles")):
        obj = getattr(p, prop_name)
        if obj is not None and obj.type == "MESH" and obj.get("fumaris_liquid_owner") == identity:
            if label != "Surface":
                _refresh_particle_nodes(obj, p.particle_radius if label == "Whitewater" else p.particle_radius*.75,
                                        label == "Whitewater")
            continue
        mesh = bpy.data.meshes.new(domain.name + " " + label)
        obj = bpy.data.objects.new(mesh.name, mesh)
        obj["fumaris_liquid_owner"] = identity
        obj["fumaris_liquid_output"] = label.lower()
        collection.objects.link(obj)
        setattr(p, prop_name, obj)
        if label == "Surface":
            material = _material(domain.name + " Water", (0.025, 0.19, 0.28), 0.12, 1)
        elif label == "Whitewater":
            material = _material(domain.name + " Whitewater", (0.96, 0.98, 1), 0.35)
            _particle_nodes(obj, material, p.particle_radius, True)
        else:
            material = _material(domain.name + " Diagnostic Particles", (0.03, 0.4, 0.8), 0.2)
            _particle_nodes(obj, material, p.particle_radius * 0.75)
        mesh.materials.append(material)
    return p


def block(header, payload, name, components=3, integer=False):
    item = header.get("blocks", {}).get(name)
    if item is None:
        return np.empty((0, components)) if components > 1 else np.empty(0)
    count, offset = int(item["count"]), int(item["offset"])
    if count < 0 or offset < 0 or offset + count * components * 4 > len(payload):
        raise RuntimeError(f"Invalid native output block: {name}")
    result = np.frombuffer(payload, dtype="<u4" if integer else "<f4", count=count * components, offset=offset)
    return result.reshape((-1, components)) if components > 1 else result


def _mesh(mesh, positions, triangles=None):
    # Point output has stable topology in most frames. Retain its attributes
    # and allocation instead of destroying/recreating every Blender element.
    if triangles is None and len(mesh.vertices) == len(positions) and not mesh.polygons:
        if len(positions):
            mesh.vertices.foreach_set("co", np.ascontiguousarray(positions, dtype=np.float32).ravel())
        mesh.update()
        return
    mesh.clear_geometry()
    mesh.vertices.add(len(positions))
    if len(positions):
        mesh.vertices.foreach_set("co", np.ascontiguousarray(positions, dtype=np.float32).ravel())
    if triangles is not None and len(triangles):
        if int(triangles.max()) >= len(positions):
            raise RuntimeError("Native surface triangles reference missing vertices")
        mesh.loops.add(len(triangles) * 3)
        mesh.loops.foreach_set("vertex_index", np.asarray(triangles, dtype=np.int32).ravel())
        mesh.polygons.add(len(triangles))
        mesh.polygons.foreach_set("loop_start", np.arange(len(triangles), dtype=np.int32) * 3)
        mesh.polygons.foreach_set("loop_total", np.full(len(triangles), 3, dtype=np.int32))
        mesh.polygons.foreach_set("use_smooth", np.ones(len(triangles), dtype=bool))
    mesh.update()


def _attribute(mesh, name, values, integer=False):
    if len(values) != len(mesh.vertices):
        return
    attr = mesh.attributes.get(name)
    if attr is None:
        attr = mesh.attributes.new(name, "INT" if integer else "FLOAT_VECTOR", "POINT")
    attr.data.foreach_set("value" if integer else "vector", np.asarray(values, dtype=np.int32 if integer else np.float32).ravel())


def apply(scene, domain, header, payload):
    _last_outputs[domain.as_pointer()] = (dict(header), payload)
    p = ensure_outputs(scene, domain)
    vertices = block(header, payload, "vertices")
    triangles = block(header, payload, "triangles", integer=True)
    positions = block(header, payload, "positions")
    whitewater = block(header, payload, "whitewater_positions")
    from . import preview
    preview.update(domain, positions, whitewater, block(header, payload, "whitewater_types", 1, True))
    if p.display == "PREVIEW" and not header.get("surface", True):
        for obj in (p.surface_object, p.particle_object, p.whitewater_object):
            if len(obj.data.vertices):
                obj.data.clear_geometry()
    else:
        if header.get("surface", True):
            _mesh(p.surface_object.data, vertices, triangles)
        else:
            p.surface_object.data.clear_geometry()
        _mesh(p.particle_object.data, positions)
        _attribute(p.particle_object.data, "velocity", block(header, payload, "velocities"))
        _mesh(p.whitewater_object.data, whitewater)
        _attribute(p.whitewater_object.data, "velocity", block(header, payload, "whitewater_velocities"))
        _attribute(p.whitewater_object.data, "whitewater_type", block(header, payload, "whitewater_types", 1, True), True)
    update_visibility(domain)
    p.status = f"Frame {header.get('frame', scene.frame_current)} · {len(positions):,} liquid · {len(whitewater):,} whitewater"
    if p.display == "PREVIEW":
        p.status += " · GPU preview"
    if "simulation_ms" in header:
        p.status += f" · {header['simulation_ms']:.1f} ms sim"
    if header.get("emission_clipped", 0):
        p.status += f" · {header['emission_clipped']:,} source samples excluded by solids"
    diagnostics = header.get("diagnostics", {})
    p.runtime_warning = ""
    if diagnostics.get("viscosity_status") == 2:
        p.runtime_warning = "Implicit viscosity encountered a numerical failure; lower viscosity or increase particle resolution"
    elif diagnostics.get("viscosity_status") == 3 and diagnostics.get("viscosity_residual", 0) > p.viscosity_tolerance:
        p.runtime_warning = "Implicit viscosity reached its iteration limit above tolerance; increase viscosity iterations"


def update_visibility(domain):
    p = domain.fumaris_liquid
    for obj, hidden in ((p.surface_object, p.display != "SURFACE"),
                        (p.particle_object, p.display != "PARTICLES"),
                        (p.whitewater_object, not p.whitewater or p.display == "PREVIEW")):
        if obj is not None:
            for scene in domain.users_scene:
                for layer in scene.view_layers:
                    if obj.name in layer.objects:
                        obj.hide_set(hidden, view_layer=layer)
            # Viewport-only preview must not hide recorded surface output in a render.
            obj.hide_render = (not p.whitewater if obj == p.whitewater_object else
                               p.display == "PARTICLES" if obj == p.surface_object else p.display != "PARTICLES")


def refresh_mode(scene, domain):
    """Restore diagnostic/cached output immediately, even with a paused solver."""
    update_visibility(domain)
    key=domain.as_pointer()
    result=_last_outputs.get(key)
    if result is None:
        if domain.fumaris_liquid.domain_id:
            from .session import cache_for
            try:
                result=cache_for(domain).read(scene.frame_current)
            except FileNotFoundError:
                return
    if result is None:
        return
    header,payload=result
    if domain.fumaris_liquid.display == 'SURFACE' and not header.get('surface',True):
        from . import meshing
        meshing.request(scene,domain,header,payload)
    else:
        apply(scene,domain,header,payload)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def clear(domain):
    _last_outputs.pop(domain.as_pointer(), None)
    from . import meshing
    meshing.cancel(domain)
    from . import preview
    preview.clear(domain)
    p = domain.fumaris_liquid
    for obj in (p.surface_object, p.whitewater_object, p.particle_object):
        if obj is not None and obj.get("fumaris_liquid_owner") == p.domain_id:
            obj.data.clear_geometry()


def clear_runtime():
    _last_outputs.clear()
    from . import meshing
    meshing.shutdown()
