"""Aquaris Blender integration. The native worker simulates on a grid (PIC/FLIP) in Vulkan."""

bl_info = {
    "name": "Aquaris",
    "author": "Fumaris",
    "version": (0, 2, 18),
    "blender": (5, 2, 0),
    "location": "Properties > Physics; Viewport > Aquaris",
    "description": "Interactive Vulkan liquid simulation",
    "category": "Physics",
}

from . import properties, operators, ui, session, preview


def register():
    properties.register()
    operators.register()
    session.register()
    ui.register()
    preview.register()


def unregister():
    preview.unregister()
    session.unregister()
    ui.unregister()
    operators.unregister()
    properties.unregister()
