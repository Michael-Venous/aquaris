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
        p.mist_object = None
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


def _liquid_material(name):
    """Clear refractive liquid whose colour comes from depth, not from its surface.

    A tinted Base Color on a transmissive surface darkens every ray equally and
    reads as coloured glass. Volume absorption instead leaves thin sheets and
    droplets clear and deep water saturated, as real liquid does.
    """
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tint, density, ior = (0.55, 0.85, 0.95), 0.35, 1.333
    material.diffuse_color = (*tint, 1)
    nodes, links = material.node_tree.nodes, material.node_tree.links
    shader = nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (1, 1, 1, 1)
    shader.inputs["Roughness"].default_value = 0.0
    shader.inputs["Transmission Weight"].default_value = 1.0
    shader.inputs["IOR"].default_value = ior
    absorption = nodes.new("ShaderNodeVolumeAbsorption")
    absorption.location = (shader.location.x, shader.location.y - 720)
    absorption.inputs["Color"].default_value = (*tint, 1)
    absorption.inputs["Density"].default_value = density
    output = next(node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial")
    links.new(absorption.outputs["Volume"], output.inputs["Volume"])
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


WHITEWATER_RADIUS = "Whitewater Radius"


def _same(first, second):
    """Node sockets hold single precision; an absolute tolerance misreads large radii as edits."""
    return abs(first - second) <= 1e-6 * max(1.0, abs(first), abs(second))


def _whitewater_nodes(obj, material, radius, group=None):
    """Whitewater as a renderer point cloud.

    Points render as true spheres and Cycles blurs them along their `velocity`
    attribute, so spray in a fast fall streaks like the water around it;
    instanced spheres stayed sharp dots.
    """
    if group is None:
        group = bpy.data.node_groups.new(obj.name + " Display", "GeometryNodeTree")
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        modifier = obj.modifiers.new("Editable Particle Display", "NODES")
        modifier.node_group = group
    else:
        group.nodes.clear()
    nodes, links = group.nodes, group.links
    source = nodes.new("NodeGroupInput")
    source.location = (-600, 100)
    points = nodes.new("GeometryNodeMeshToPoints")
    points.mode = "VERTICES"
    points.location = (-160, 100)
    type_attr = nodes.new("GeometryNodeInputNamedAttribute")
    type_attr.data_type = "INT"
    type_attr.inputs["Name"].default_value = "whitewater_type"
    type_attr.label = "1 spray / 2 foam / 3 bubble"
    type_attr.location = (-820, -160)
    foam = nodes.new("ShaderNodeMath")
    foam.operation = "COMPARE"
    foam.inputs[1].default_value = 2
    foam.inputs[2].default_value = 0.1
    foam.location = (-600, -160)
    scale = nodes.new("ShaderNodeMath")
    scale.operation = "MULTIPLY_ADD"
    scale.inputs[1].default_value = 0.08
    scale.inputs[2].default_value = 0.10
    scale.location = (-380, -160)
    size = nodes.new("ShaderNodeMath")
    size.operation = "MULTIPLY"
    size.name = WHITEWATER_RADIUS
    size.label = "x particle radius x Particle Size"
    size.inputs[1].default_value = radius
    size.location = (-380, -360)
    shade = nodes.new("GeometryNodeSetMaterial")
    shade.location = (60, 100)
    shade.inputs["Material"].default_value = material
    output = nodes.new("NodeGroupOutput")
    output.location = (280, 100)
    links.new(source.outputs["Geometry"], points.inputs["Mesh"])
    links.new(type_attr.outputs["Attribute"], foam.inputs[0])
    links.new(foam.outputs[0], scale.inputs[0])
    links.new(scale.outputs[0], size.inputs[0])
    links.new(size.outputs[0], points.inputs["Radius"])
    links.new(points.outputs["Points"], shade.inputs["Geometry"])
    links.new(shade.outputs["Geometry"], output.inputs["Geometry"])
    obj["fumaris_liquid_display_version"] = 3
    obj["fumaris_liquid_display_radius"] = float(size.inputs[1].default_value)


def _refresh_whitewater_nodes(obj, radius):
    modifier = next((value for value in obj.modifiers if value.type == "NODES" and
                     value.name == "Editable Particle Display" and value.node_group), None)
    if modifier is None or modifier.node_group.users > 1:
        return
    group = modifier.node_group
    version = obj.get("fumaris_liquid_display_version")
    if version == 3:
        size = group.nodes.get(WHITEWATER_RADIUS)
        if size is None or size.bl_idname != "ShaderNodeMath" or size.operation != "MULTIPLY":
            return
        current = float(size.inputs[1].default_value)
        if not _same(current, float(obj.get("fumaris_liquid_display_radius", radius))):
            return  # The user edited this radius directly in Geometry Nodes.
        if not _same(current, radius):
            size.inputs[1].default_value = radius
        obj["fumaris_liquid_display_radius"] = float(size.inputs[1].default_value)
        return
    if version == 2 and "fumaris_liquid_display_radius_factor" not in obj:
        # The untouched instanced-sphere graph of 0.2.8 and earlier becomes the
        # point cloud in place; an edited one stays as its author left it.
        nodes = _canonical_particle_graph(group, True)
        if nodes is not None:
            current = float(nodes["GeometryNodeMeshIcoSphere"].inputs["Radius"].default_value)
            if (_same(float(nodes["GeometryNodeMeshToPoints"].inputs["Radius"].default_value), current) and
                    _same(current, float(obj.get("fumaris_liquid_display_radius", current)))):
                _whitewater_nodes(obj, nodes["GeometryNodeSetMaterial"].inputs["Material"].default_value, radius, group)
                return
    _refresh_particle_nodes(obj, radius, True)


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
    if not _same(float(points.inputs["Radius"].default_value), current_radius):
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
    if not _same(current_radius, previous):
        return  # The user edited this radius directly in Geometry Nodes.
    target = radius * float(obj.get("fumaris_liquid_display_radius_factor", 1.0))
    if not _same(current_radius, target):
        points.inputs["Radius"].default_value = target
        sphere.inputs["Radius"].default_value = target
    obj["fumaris_liquid_display_radius"] = float(sphere.inputs["Radius"].default_value)


MIST_MODIFIER, MIST_VOLUME = "Aquaris Mist", "Mist Volume"


def _mist_nodes(obj, material):
    group = bpy.data.node_groups.new(obj.name + " Display", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = group.nodes, group.links
    source, output = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    points = nodes.new("GeometryNodeMeshToPoints")
    points.mode = "VERTICES"
    # Bubbles are under water; only spray and foam hang in the air.
    kind = nodes.new("GeometryNodeInputNamedAttribute")
    kind.data_type = "INT"
    kind.inputs["Name"].default_value = "whitewater_type"
    kind.label = "1 spray / 2 foam / 3 bubble"
    bubble = nodes.new("FunctionNodeCompare")
    bubble.data_type, bubble.operation = "INT", "EQUAL"
    sockets = {socket.name: socket for socket in bubble.inputs if socket.enabled}
    sockets["B"].default_value = 3
    airborne = nodes.new("GeometryNodeDeleteGeometry")
    airborne.domain = "POINT"
    volume = nodes.new("GeometryNodePointsToVolume")
    volume.name = MIST_VOLUME
    volume.inputs["Resolution Mode"].default_value = "Size"
    shade = nodes.new("GeometryNodeSetMaterial")
    shade.inputs["Material"].default_value = material
    for index, node in enumerate((source, points, airborne, volume, shade, output)):
        node.location = (-600 + 220 * index, 100)
    kind.location, bubble.location = (-600, -160), (-380, -160)
    links.new(source.outputs["Geometry"], points.inputs["Mesh"])
    links.new(kind.outputs["Attribute"], sockets["A"])
    links.new(bubble.outputs["Result"], airborne.inputs["Selection"])
    links.new(points.outputs["Points"], airborne.inputs["Geometry"])
    links.new(airborne.outputs["Geometry"], volume.inputs["Points"])
    links.new(volume.outputs["Volume"], shade.inputs["Geometry"])
    links.new(shade.outputs["Geometry"], output.inputs["Geometry"])
    modifier = obj.modifiers.new(MIST_MODIFIER, "NODES")
    modifier.node_group = group


def _sync_layers(domain):
    """A just-linked object is not in a view layer's object list, so it cannot be hidden yet."""
    for scene in domain.users_scene:
        for layer in scene.view_layers:
            try:
                layer.update()
            except RuntimeError:
                pass  # Not permitted in this context; visibility follows on the next update.


def _ensure_mist(domain, collection, identity):
    """Optional soft volume from spray and foam; shares the whitewater points."""
    p = domain.fumaris_liquid
    whitewater = p.whitewater_object
    if whitewater is None or whitewater.get("fumaris_liquid_owner") != identity:
        return
    obj = p.mist_object
    if obj is None or obj.type != "MESH" or obj.get("fumaris_liquid_owner") != identity:
        if p.whitewater_mist <= 0:
            return  # Nothing is created until mist is asked for.
        obj = bpy.data.objects.new(domain.name + " Mist", whitewater.data)
        obj["fumaris_liquid_owner"] = identity
        obj["fumaris_liquid_output"] = "mist"
        collection.objects.link(obj)
        p.mist_object = obj
        _sync_layers(domain)
        material = bpy.data.materials.new(domain.name + " Mist")
        material.use_nodes = True
        nodes, links = material.node_tree.nodes, material.node_tree.links
        nodes.remove(nodes["Principled BSDF"])
        scatter = nodes.new("ShaderNodeVolumePrincipled")
        scatter.inputs["Color"].default_value = (1, 1, 1, 1)
        scatter.inputs["Density"].default_value = 1.0
        scatter.inputs["Anisotropy"].default_value = 0.6
        output = next(node for node in nodes if node.bl_idname == "ShaderNodeOutputMaterial")
        links.new(scatter.outputs["Volume"], output.inputs["Volume"])
        _mist_nodes(obj, material)
    elif not obj.users_collection:
        collection.objects.link(obj)
        _sync_layers(domain)
    if obj.data is not whitewater.data:
        obj.data = whitewater.data
    modifier = obj.modifiers.get(MIST_MODIFIER)
    # A removed, replaced or shared graph is an intentional customization.
    volume = (modifier.node_group.nodes.get(MIST_VOLUME) if modifier is not None and modifier.type == "NODES" and
              modifier.node_group is not None and modifier.node_group.users == 1 else None)
    if volume is not None and volume.bl_idname == "GeometryNodePointsToVolume" and p.whitewater_mist > 0:
        reach = p.particle_radius * p.whitewater_mist_radius
        for name, value in (("Density", p.whitewater_mist), ("Radius", reach), ("Voxel Size", reach / 3)):
            if not _same(volume.inputs[name].default_value, value):
                volume.inputs[name].default_value = value


def refresh_look(domain):
    """Apply whitewater size and mist settings to existing outputs without reloading a frame."""
    p = domain.fumaris_liquid
    if not p.domain_id:
        return
    ensure_identity(domain)  # A duplicated domain must not restyle or extend the original's outputs.
    if p.output_collection is None or p.whitewater_object is None or p.whitewater_object.get("fumaris_liquid_owner") != p.domain_id:
        return
    _refresh_whitewater_nodes(p.whitewater_object, p.particle_radius * p.whitewater_size)
    _ensure_mist(domain, p.output_collection, p.domain_id)
    update_visibility(domain)


def ensure_outputs(scene, domain):
    identity = ensure_identity(domain)
    p = domain.fumaris_liquid
    collection = p.output_collection
    if collection is None or collection.get("fumaris_liquid_owner") != identity:
        collection = bpy.data.collections.new(domain.name + " Output")
        # inside the domain's own collection (a Quick Setup's "Aquaris ..."), not beside it
        home = next((c for c in domain.users_collection
                     if c == scene.collection or c in scene.collection.children_recursive), scene.collection)
        home.children.link(collection)
        collection["fumaris_liquid_owner"] = identity
        collection["fumaris_liquid_domain"] = domain
        p.output_collection = collection
    for prop_name, label in (("surface_object", "Surface"), ("whitewater_object", "Whitewater"), ("particle_object", "Particles")):
        obj = getattr(p, prop_name)
        if obj is not None and obj.type == "MESH" and obj.get("fumaris_liquid_owner") == identity:
            if not obj.users_collection:
                # Deleted in the viewport: the pointer kept it alive but nothing shows it.
                collection.objects.link(obj)
            if label == "Whitewater":
                _refresh_whitewater_nodes(obj, p.particle_radius * p.whitewater_size)
            elif label != "Surface":
                _refresh_particle_nodes(obj, p.particle_radius*.75, False)
            continue
        mesh = bpy.data.meshes.new(domain.name + " " + label)
        obj = bpy.data.objects.new(mesh.name, mesh)
        obj["fumaris_liquid_owner"] = identity
        obj["fumaris_liquid_output"] = label.lower()
        collection.objects.link(obj)
        setattr(p, prop_name, obj)
        if label == "Surface":
            material = _liquid_material(domain.name + " Water")
        elif label == "Whitewater":
            material = _material(domain.name + " Whitewater", (0.96, 0.98, 1), 0.6)
            # Foam scatters light inside itself rather than looking like painted beads.
            material.node_tree.nodes["Principled BSDF"].inputs["Subsurface Weight"].default_value = 0.4
            _whitewater_nodes(obj, material, p.particle_radius * p.whitewater_size)
        else:
            material = _material(domain.name + " Diagnostic Particles", (0.03, 0.4, 0.8), 0.2)
            _particle_nodes(obj, material, p.particle_radius * 0.75)
        mesh.materials.append(material)
    _ensure_mist(domain, collection, identity)
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


def _set_positions(mesh, positions):
    # The attribute arrays are written with one copy. vertices.foreach_set("co")
    # and loops.foreach_set("vertex_index") go through per-element property
    # access: 165 ms and 425 ms against 1 ms and 2 ms for a 3.7M-triangle frame.
    values = np.ascontiguousarray(positions, dtype=np.float32).ravel()
    attribute = mesh.attributes.get("position")
    if attribute is not None and len(attribute.data) * 3 == len(values):
        attribute.data.foreach_set("vector", values)
    else:
        mesh.vertices.foreach_set("co", values)


def _mesh(mesh, positions, triangles=None):
    # Point output has stable topology in most frames. Retain its attributes
    # and allocation instead of destroying/recreating every Blender element.
    if triangles is None and len(mesh.vertices) == len(positions) and not mesh.polygons:
        if len(positions):
            _set_positions(mesh, positions)
        mesh.update()
        return
    mesh.clear_geometry()
    mesh.vertices.add(len(positions))
    if len(positions):
        _set_positions(mesh, positions)
    if triangles is not None and len(triangles):
        if int(triangles.max()) >= len(positions):
            raise RuntimeError("Native surface triangles reference missing vertices")
        mesh.loops.add(len(triangles) * 3)
        corners = np.asarray(triangles, dtype=np.int32).ravel()
        attribute = mesh.attributes.get(".corner_vert")
        if attribute is not None and len(attribute.data) == len(corners):
            attribute.data.foreach_set("value", corners)
        else:
            mesh.loops.foreach_set("vertex_index", corners)
        mesh.polygons.add(len(triangles))
        # Face sizes follow from the offsets, and faces are smooth unless a
        # sharp_face attribute says otherwise, so neither is written.
        mesh.polygons.foreach_set("loop_start", np.arange(len(triangles), dtype=np.int32) * 3)
    mesh.update()


def _attribute(mesh, name, values, integer=False, scalar=False):
    """A point attribute: a vector, or with `integer` / `scalar` one int / float per point."""
    if len(values) != len(mesh.vertices):
        return
    kind = "INT" if integer else "FLOAT" if scalar else "FLOAT_VECTOR"
    attr = mesh.attributes.get(name)
    if attr is not None and attr.data_type != kind:
        mesh.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = mesh.attributes.new(name, kind, "POINT")
    attr.data.foreach_set("vector" if kind == "FLOAT_VECTOR" else "value",
                          np.asarray(values, dtype=np.int32 if integer else np.float32).ravel())


def apply(scene, domain, header, payload):
    _last_outputs[domain.as_pointer()] = (dict(header), payload)
    p = ensure_outputs(scene, domain)
    vertices = block(header, payload, "vertices")
    triangles = block(header, payload, "triangles", integer=True)
    positions = block(header, payload, "positions")
    whitewater = block(header, payload, "whitewater_positions")
    from . import preview
    # The preview draws the meshed surface from these arrays; a surface made
    # only for it (nothing recorded) is not written into Blender's mesh.
    preview.update(domain, positions, whitewater, block(header, payload, "whitewater_types", 1, True),
                   mesh=(vertices, triangles, block(header, payload, "vertex_normals")) if p.display == "PREVIEW" and header.get("surface", True) and len(triangles) else None)
    if p.display == "PREVIEW" and (not header.get("surface", True) or header.get("preview_only")):
        for obj in (p.surface_object, p.particle_object, p.whitewater_object):
            if len(obj.data.vertices):
                obj.data.clear_geometry()
    else:
        if header.get("surface", True):
            _mesh(p.surface_object.data, vertices, triangles)
            # Cycles motion-blurs a mesh from a point attribute named "velocity".
            motion = block(header, payload, "vertex_velocities")
            if len(motion) == len(vertices) and len(vertices):
                _attribute(p.surface_object.data, "velocity", motion)
        else:
            p.surface_object.data.clear_geometry()
        _mesh(p.particle_object.data, positions)
        _attribute(p.particle_object.data, "velocity", block(header, payload, "velocities"))
        # A particle's number from its birth to its removal: stable across frames, for instancing and Geometry Nodes.
        _attribute(p.particle_object.data, "id", block(header, payload, "ids", 1, True), True)
        # Seconds since the particle was emitted (to the frame), for colouring or fading by age.
        _attribute(p.particle_object.data, "age", block(header, payload, "ages", 1), scalar=True)
        _mesh(p.whitewater_object.data, whitewater)
        _attribute(p.whitewater_object.data, "velocity", block(header, payload, "whitewater_velocities"))
        _attribute(p.whitewater_object.data, "whitewater_type", block(header, payload, "whitewater_types", 1, True), True)
        # Seconds this whitewater particle has left (foam dissolves when it reaches 0), for fading it out.
        _attribute(p.whitewater_object.data, "whitewater_life", block(header, payload, "whitewater_life", 1), scalar=True)
    update_visibility(domain)
    p.status = f"Frame {header.get('frame', scene.frame_current)} · {len(positions):,} liquid"
    if len(whitewater):
        p.status += f" · {len(whitewater):,} whitewater"
    if p.display == "PREVIEW":
        p.status += " · GPU preview"
    if "simulation_ms" in header:
        p.status += f" · {header['simulation_ms']:.1f} ms sim"
    if header.get("emission_clipped", 0):
        p.status += f" · {header['emission_clipped']:,} source samples excluded by solids"
    p.runtime_warning = ""
    if header.get("mesh_fallback"):
        p.runtime_warning = "GPU mesher could not take this frame and Fast was used: " + str(header["mesh_fallback"])[:160]


# Set while an export plays the cache through: the outputs being exported must stay visible (a hidden object is
# not selected, so not exported) whatever the display mode would show.
exporting = False


def update_visibility(domain):
    if exporting:
        return
    p = domain.fumaris_liquid
    for obj, hidden in ((p.surface_object, p.display != "SURFACE"),
                        (p.particle_object, p.display != "PARTICLES"),
                        (p.whitewater_object, not p.whitewater or p.display == "PREVIEW"),
                        # Shares the whitewater points, so it is theirs to clear.
                        (p.mist_object, not p.whitewater or p.whitewater_mist <= 0 or p.display != "SURFACE")):
        if obj is not None:
            for scene in domain.users_scene:
                for layer in scene.view_layers:
                    if obj.name in layer.objects:
                        obj.hide_set(hidden, view_layer=layer)
            # Viewport-only preview must not hide recorded surface output in a render.
            obj.hide_render = (not p.whitewater or p.whitewater_mist <= 0 if obj == p.mist_object else
                               not p.whitewater if obj == p.whitewater_object else
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
    from . import masks, meshing
    masks.clear()
    meshing.shutdown()
