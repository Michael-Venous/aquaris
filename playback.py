"""Play, pause and stop the liquid from the 3D viewport's and the timeline's headers, or by shortcut, whatever is
selected (as Fumaris does for smoke): the running domain, else the one chosen in the header's menu, else the
selected one, else the only domain in the scene. Shortcuts: Shift+Alt+L play / pause, Shift+Alt+K stop,
editable in the add-on's preferences.
"""
import bpy
from bpy.props import EnumProperty, PointerProperty, StringProperty

from . import operators, session, devices

_KEYMAPS = []


def is_domain(obj):
    return obj is not None and obj.fumaris_liquid.role == "DOMAIN"


def scene_domains(context):
    return [obj for obj in context.view_layer.objects if is_domain(obj)]


def resolve_domain(context):
    running = session.active()
    if running is not None:
        return running.domain
    chosen = getattr(context.scene, "fumaris_liquid_playback_domain", None)
    domains = scene_domains(context)
    if chosen is not None and chosen in domains:
        return chosen
    selected = operators.domain_for(context)
    if selected is not None:
        return selected
    return domains[0] if len(domains) == 1 else None


def _control(context, domain, action):
    """The domain's own control operator, run with that domain as the context object."""
    with context.temp_override(object=domain, active_object=domain):
        return bpy.ops.fumaris_liquid.control(action=action)


class LIQUID_OT_playback_toggle(bpy.types.Operator):
    bl_idname = "fumaris_liquid.playback_toggle"
    bl_label = "Aquaris Play / Pause"

    @classmethod
    def description(cls, context, properties):
        return f"Play the liquid live, pause it, or resume it, without changing the selection ({shortcut(cls.bl_idname)})"

    @classmethod
    def poll(cls, context):
        running = session.active()
        if running is not None and running.bake:
            cls.poll_message_set("Stop the bake before starting a live preview")
            return False
        if resolve_domain(context) is None:
            cls.poll_message_set("Choose an Aquaris domain in the header's Aquaris menu")
            return False
        return True

    def execute(self, context):
        domain = resolve_domain(context)
        running = session.active()
        action = "PLAY" if running is None else ("RESUME" if running.paused else "PAUSE")
        result = _control(context, domain, action)
        return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}


class LIQUID_OT_playback_stop(bpy.types.Operator):
    bl_idname = "fumaris_liquid.playback_stop"
    bl_label = "Aquaris Stop"
    bl_description = "Stop the running liquid simulation or bake"

    @classmethod
    def poll(cls, context):
        return session.active() is not None

    def execute(self, context):
        running = session.active()
        result = _control(context, running.domain, "STOP")
        return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}


def draw_controls(layout, context, compact=False):
    running = session.active()
    paused = running is not None and running.paused
    row = layout.row(align=True)
    if compact:
        row.popover(panel="LIQUID_PT_playback", text="Aquaris")
    row.operator("fumaris_liquid.playback_toggle", text="" if compact else ("Resume" if paused else "Pause" if running else "Play"),
                 icon="PAUSE" if running is not None and not paused else "PLAY")
    row.operator("fumaris_liquid.playback_stop", text="" if compact else "Stop", icon="SNAP_FACE")


class LIQUID_PT_playback(bpy.types.Panel):
    bl_label = "Aquaris Playback"
    bl_idname = "LIQUID_PT_playback"
    bl_space_type = "VIEW_3D"
    bl_region_type = "HEADER"

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.enabled = session.active() is None
        row.prop(context.scene, "fumaris_liquid_playback_domain", text="Domain")
        draw_controls(layout, context)
        domain = resolve_domain(context)
        layout.label(text=("Domain: " + domain.name) if domain else "Choose a domain", icon="MOD_FLUIDSIM")
        if domain is not None and domain.fumaris_liquid.status:
            layout.label(text=domain.fumaris_liquid.status)
        layout.label(text=f"{shortcut('fumaris_liquid.playback_toggle')} play / pause, {shortcut('fumaris_liquid.playback_stop')} stop",
                     icon="KEYINGSET")
        layout.label(text="Change them in the add-on's preferences")


def shortcut(operator):
    """The key the user has for an operator now (Window keymap, user configuration), as Blender writes it."""
    try:
        config = bpy.context.window_manager.keyconfigs.user
        keymap = config.keymaps.get("Window") if config else None
        for item in (keymap.keymap_items if keymap else ()):
            if item.idname == operator and item.active:
                keys = [name for flag, name in ((item.ctrl, "Ctrl"), (item.shift, "Shift"), (item.alt, "Alt"), (item.oskey, "Cmd")) if flag]
                return "+".join(keys + [item.type.title()])
    except Exception:
        pass
    return "no key"


@bpy.app.handlers.persistent
def _native_playback(scene, *_):
    """Blender's own timeline playback takes the scene's frames: a live preview, which sets them too, stops (a bake runs on)."""
    running = session.active()
    if running is not None and not running.bake and running.scene == scene:
        running.request_stop()


def draw_header(self, context):
    if context.area.type == "DOPESHEET_EDITOR" and context.space_data.mode != "TIMELINE":
        return
    if session.active() is None and not scene_domains(context):
        return
    draw_controls(self.layout, context, compact=True)


def draw_keymaps(layout, context):
    import rna_keymap_ui
    config = context.window_manager.keyconfigs.user
    keymap = config.keymaps.get("Window") if config else None
    if keymap is None:
        return
    layout.label(text="Liquid playback shortcuts (whatever is selected)")
    for item in keymap.keymap_items:
        if item.idname in {"fumaris_liquid.playback_toggle", "fumaris_liquid.playback_stop"}:
            rna_keymap_ui.draw_kmi([], config, keymap, item, layout, 0)


class LIQUID_OT_refresh_gpus(bpy.types.Operator):
    bl_idname = "fumaris_liquid.refresh_gpus"
    bl_label = "Refresh GPUs"
    bl_description = "List available simulation GPUs without starting the solver"

    def execute(self, context):
        from .bridge import find_worker
        try:
            found = devices.refresh(find_worker())
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Found {len(found)} simulation GPU(s)")
        return {"FINISHED"}


def draw_gpu_selection(layout, context, preferences=None):
    import os
    addon = context.preferences.addons.get(__package__)
    preferences = preferences or (addon.preferences if addon else None)
    if preferences is None:
        return
    column = layout.column(align=True)
    column.enabled = session.active() is None
    column.prop(preferences, "simulation_gpu")
    if preferences.simulation_gpu == "DEVICE":
        row = column.row(align=True)
        row.prop(preferences, "simulation_gpu_device", text="Device")
        row.operator("fumaris_liquid.refresh_gpus", text="", icon="FILE_REFRESH")
        if devices._error:
            column.label(text=devices._error, icon="ERROR")
        elif devices._loaded and not devices._devices:
            column.label(text="No supported hardware GPUs found", icon="ERROR")
        elif (devices._loaded and preferences.simulation_gpu_uuid and
              not any(d["uuid"] == preferences.simulation_gpu_uuid for d in devices._devices)):
            column.label(text="Selected GPU unavailable; choose another or Auto", icon="ERROR")
    elif preferences.simulation_gpu == "NAMED":
        column.prop(preferences, "simulation_gpu_name")
    if os.environ.get("FUMARIS_LIQUID_GPU"):
        column.label(text="Environment GPU override active", icon="INFO")
    if session.active() is not None:
        layout.label(text="Stop the simulation to change GPU", icon="INFO")


class LIQUID_preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    simulation_gpu: EnumProperty(
        name="Simulation GPU", default="AUTO",
        items=(("AUTO", "Auto", "A GPU Blender is not drawing on when the computer has another of the same kind; "
                "otherwise Blender's. Sharing a GPU with Blender's drawing slows live previews", 0),
               ("DEVICE", "Choose GPU", "Select a detected graphics card, saved by its unique device identity", 3),
               ("BLENDER", "Blender's GPU", "The GPU Blender draws on", 1),
               ("NAMED", "Named", "The GPU whose name contains Name, or whose number is Name (0 is the first)", 2)),
        description="GPU the liquid simulates on. Takes effect when the simulation next starts")
    simulation_gpu_name: StringProperty(
        name="Name", default="",
        description="Part of the GPU's name, such as 4060, or its number. The GPU in use is shown in the Domain's Diagnostics")

    simulation_gpu_uuid: StringProperty(name="Simulation GPU UUID", default="", options={"HIDDEN"})
    simulation_gpu_label: StringProperty(name="Saved GPU Name", default="", options={"HIDDEN"})
    simulation_gpu_device: EnumProperty(
        name="Device", items=devices.items, get=devices.get_selection, set=devices.set_selection, options={"SKIP_SAVE"},
        description="GPU for the next preview, bake and GPU surface mesh; saved by UUID")

    def draw(self, context):
        from . import bl_info, bridge
        layout = self.layout
        draw_gpu_selection(layout, context, self)
        draw_keymaps(layout.box(), context)
        problem = bridge.worker_problem(bl_info["version"])
        status = layout.column(align=True)
        status.alert = bool(problem)
        status.label(text="Aquaris " + ".".join(map(str, bl_info["version"])), icon="MOD_FLUIDSIM")
        status.label(text=problem or "Solver worker found", icon="ERROR" if problem else "CHECKMARK")


def _domain_poll(scene, obj):
    return is_domain(obj) and obj.name in scene.objects


classes = (LIQUID_OT_playback_toggle, LIQUID_OT_playback_stop, LIQUID_PT_playback, LIQUID_OT_refresh_gpus, LIQUID_preferences)


def register():
    bpy.types.Scene.fumaris_liquid_playback_domain = PointerProperty(
        name="Playback Domain", type=bpy.types.Object, poll=_domain_poll,
        description="Domain the header buttons and shortcuts control. A single domain in the scene is chosen by itself")
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_HT_header.append(draw_header)
    bpy.types.DOPESHEET_HT_header.append(draw_header)
    bpy.app.handlers.animation_playback_pre.append(_native_playback)
    config = bpy.context.window_manager.keyconfigs.addon
    if config:
        keymap = config.keymaps.new(name="Window", space_type="EMPTY")
        for operator, key in (("fumaris_liquid.playback_toggle", "L"), ("fumaris_liquid.playback_stop", "K")):
            _KEYMAPS.append((keymap, keymap.keymap_items.new(operator, key, "PRESS", shift=True, alt=True)))


def unregister():
    if _native_playback in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.remove(_native_playback)
    for keymap, item in _KEYMAPS:
        keymap.keymap_items.remove(item)
    _KEYMAPS.clear()
    bpy.types.DOPESHEET_HT_header.remove(draw_header)
    bpy.types.VIEW3D_HT_header.remove(draw_header)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.fumaris_liquid_playback_domain
