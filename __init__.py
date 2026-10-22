"""Aquaris Blender integration. The native worker simulates on a grid in Vulkan compute."""

bl_info = {
    "name": "Aquaris",
    "author": "Fumaris",
    "version": (0, 3, 25),
    "blender": (5, 2, 0),
    "location": "Properties > Physics; Viewport > Aquaris",
    "description": "Interactive Vulkan liquid simulation",
    "category": "Physics",
}

from . import properties, operators, ui, session, preview, playback


def register():
    properties.register()
    operators.register()
    session.register()
    ui.register()
    preview.register()
    playback.register()


def unregister():
    playback.unregister()
    preview.unregister()
    session.unregister()
    ui.unregister()
    operators.unregister()
    properties.unregister()
