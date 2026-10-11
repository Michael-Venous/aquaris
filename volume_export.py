"""Native Blender VolumeToMesh conversion; no Python OpenVDB dependency."""

import bpy


def geometry(scene, source, props, reader):
    if source.type != "VOLUME":
        raise RuntimeError(f"{source.name}: Volume source requires a Blender Volume object; convert GN volume geometry with a Volume to Mesh node")
    mesh = bpy.data.meshes.new("Aquaris Temporary Volume Surface")
    proxy = bpy.data.objects.new(mesh.name, mesh)
    try:
        scene.collection.objects.link(proxy)
        proxy.hide_render = True
        proxy.hide_select = True
        proxy.hide_set(True)
        proxy.matrix_world = source.matrix_world.copy()
        modifier = proxy.modifiers.new("Native Volume Conversion", "VOLUME_TO_MESH")
        modifier.object = source
        modifier.grid_name = props.volume_grid
        modifier.threshold = props.volume_threshold
        modifier.adaptivity = 0
        modifier.resolution_mode = props.volume_resolution
        if props.volume_resolution == "VOXEL_SIZE":
            modifier.voxel_size = props.volume_voxel_size
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        try:
            return reader(proxy, depsgraph, props)
        except RuntimeError as exc:
            evaluated = source.evaluated_get(depsgraph)
            grids = evaluated.data.grids
            message = grids.error_message
            available = ", ".join(grid.name for grid in grids) or "none"
            raise RuntimeError(f"{source.name}: VolumeToMesh produced no usable surface for grid '{props.volume_grid}' at threshold {props.volume_threshold:g}; available grids: {available}" + (f"; {message}" if message else "")) from exc
    finally:
        bpy.data.objects.remove(proxy, do_unlink=True)
        bpy.data.meshes.remove(mesh)
