"""Aquaris Blender integration. Native solver: MIT-licensed Nebula."""

bl_info = {
    "name": "Aquaris",
    "author": "Fumaris",
    "version": (0, 2, 7),
    "blender": (5, 2, 0),
    "location": "Properties > Physics; Viewport > Aquaris",
    "description": "Interactive Vulkan liquid simulation using Nebula",
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
