"""Interactive controls and cache management with guarded ownership."""

import bpy
import json
import platform
from bpy.props import EnumProperty

from . import display, quick_setup, session


def domain_for(context):
    """Resolve only the selected liquid object, never an unrelated scene default."""
    obj = context.object
    if obj is None:
        return None
    if obj.fumaris_liquid.role == "DOMAIN":
        return obj
    owner = obj.get("fumaris_liquid_owner") if obj.get("fumaris_liquid_output") else None
    if owner:
        for value in context.scene.objects:
            if value.fumaris_liquid.role == "DOMAIN" and value.fumaris_liquid.domain_id == owner:
                return value
        return None
    collection_name = {"EMITTER": "emitter_collection", "COLLIDER": "collider_collection",
                       "EFFECTOR": "effector_collection", "OUTFLOW": "outflow_collection"}.get(obj.fumaris_liquid.role)
    if collection_name is None:
        return None
    matches = []
    for value in context.scene.objects:
        p = value.fumaris_liquid
        if p.role != "DOMAIN":
            continue
        collection = getattr(p, collection_name)
        if collection is not None and obj.name in collection.all_objects:
            matches.append(value)
    return matches[0] if len(matches) == 1 else None


class LIQUID_OT_setup(bpy.types.Operator):
    bl_idname = "fumaris_liquid.quick_setup"
    bl_label = "Quick Setup"
    bl_description = "Add an independent liquid demonstration at the 3D cursor"
    bl_options = {"REGISTER", "UNDO"}
    preset: EnumProperty(name="Preset", items=[
        ("SPLASH", "Splash (real-time)", "A ball dropped into a pool on the grid solver, sized to play back live with a surface"),
        ("REALTIME_DAM_BREAK", "Dam Break (real-time)", "A wall of water released into pillars on the grid solver, sized to play back live"),
        ("REALTIME_POUR", "Pour (real-time)", "Continuous inflow into a pool on the grid solver, sized to play back live"),
        ("DAM_BREAK", "Dam Break (particles)", "Release an initial water volume into a basin, on the particle solver"),
        ("POUR", "Pouring Water (particles)", "Continuous downward inflow into a basin, on the particle solver"),
        ("VISCOUS_POUR", "Viscous Pour (particles)", "Higher viscosity pouring example, on the particle solver"),
    ], default="SPLASH")

    @classmethod
    def poll(cls, context):
        return session.active() is None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.prop(self, "preset")

    def execute(self, context):
        if session.active() is not None:
            self.report({"ERROR"}, "Stop the current liquid simulation before adding a setup")
            return {"CANCELLED"}
        domain = quick_setup.create(context, self.preset)
        context.scene.fumaris_liquid_domain = domain
        return {"FINISHED"}


class LIQUID_OT_control(bpy.types.Operator):
    bl_idname = "fumaris_liquid.control"
    bl_label = "Liquid Simulation"
    action: EnumProperty(items=[(value, value.title(), "") for value in
                                ("PLAY", "PAUSE", "RESUME", "STOP", "BAKE", "CANCEL", "RESET")])

    @classmethod
    def poll(cls, context):
        return domain_for(context) is not None

    def execute(self, context):
        domain = domain_for(context)
        running = session.active()
        try:
            if domain is None:
                raise RuntimeError("Select an Aquaris domain, output or assigned participant")
            if running is not None and running.domain != domain:
                raise RuntimeError("Another Aquaris domain is running; select its object to control it")
            if self.action in {"PLAY", "BAKE"}:
                session.start(context.scene, domain, self.action == "BAKE")
            elif self.action in {"STOP", "CANCEL"}:
                if running is not None:
                    running.request_stop()
            elif self.action == "PAUSE":
                if running is not None:
                    running.paused = True
            elif self.action == "RESUME":
                if running is not None:
                    running.paused = False
            elif self.action == "RESET":
                if running is not None:
                    raise RuntimeError("Stop the simulation before resetting its output")
                display.clear(domain)
                domain.fumaris_liquid.status = "Ready"
                domain.fumaris_liquid.last_error = ""
        except Exception as exc:
            if domain is not None:
                domain.fumaris_liquid.last_error = str(exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class LIQUID_OT_cache(bpy.types.Operator):
    bl_idname = "fumaris_liquid.cache"
    bl_label = "Liquid Cache"
    action: EnumProperty(items=[("LOAD", "Load Current Frame", "Load the selected slot's current frame"),
                                ("DELETE", "Delete Cache Slot", "Delete only this domain's owned cache slot")])

    @classmethod
    def poll(cls, context):
        return domain_for(context) is not None and session.active() is None

    def invoke(self, context, event):
        if self.action == "DELETE":
            return context.window_manager.invoke_confirm(self, event, title="Delete Liquid Cache Slot?",
                message="Remove every recorded frame in this domain's selected cache slot")
        return self.execute(context)

    def execute(self, context):
        domain = domain_for(context)
        try:
            cache = session.cache_for(domain)
            if self.action == "DELETE":
                cache.delete()
                display.clear(domain)
                domain.fumaris_liquid.status = "Cache slot deleted"
            else:
                header, payload = cache.read(context.scene.frame_current)
                display.apply(context.scene, domain, header, payload)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            domain.fumaris_liquid.last_error = str(exc)
            return {"CANCELLED"}
        return {"FINISHED"}


class LIQUID_OT_export_alembic(bpy.types.Operator):
    bl_idname = "fumaris_liquid.export_alembic"
    bl_label = "Export Surface Alembic"
    bl_description = "Export the selected slot's cached surface over its saved frame range"
    filepath: bpy.props.StringProperty(subtype="FILE_PATH", default="liquid_surface.abc")

    @classmethod
    def poll(cls, context):
        return domain_for(context) is not None and session.active() is None

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        domain = domain_for(context)
        selected, current = list(context.selected_objects), context.view_layer.objects.active
        old_playback = domain.fumaris_liquid.cache_playback
        old_frame = context.scene.frame_current
        try:
            frames = session.cache_for(domain).frames()
            if not frames:
                raise RuntimeError("The selected cache slot contains no frames")
            if frames != list(range(frames[0], frames[-1] + 1)):
                raise RuntimeError("Alembic export requires a contiguous cache range")
            display.ensure_outputs(context.scene, domain)
            for obj in selected:
                obj.select_set(False)
            domain.fumaris_liquid.surface_object.select_set(True)
            context.view_layer.objects.active = domain.fumaris_liquid.surface_object
            domain.fumaris_liquid.cache_playback = True
            bpy.ops.wm.alembic_export(filepath=bpy.path.abspath(self.filepath),
                                     start=frames[0], end=frames[-1], selected=True,
                                     as_background_job=False, flatten=True)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            context.scene.frame_set(old_frame)
            domain.fumaris_liquid.cache_playback = old_playback
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in selected:
                if obj.name in context.scene.objects:
                    obj.select_set(True)
            context.view_layer.objects.active = current
        return {"FINISHED"}


QUALITY_LEVELS = {
    # Two pressure iterations halve the particle solve (splashes are softer,
    # about a third of the crown) and the surface is built on the GPU. A cell
    # of 1.5 radii halves the triangles Blender has to take in each frame:
    # 5.5 against 3.3 frames a second at 207k particles on the grid solver.
    "DRAFT": {"iterations": 2, "mesh_quality": "GPU_SMOOTH", "mesh_cell_relative": True, "mesh_cell_factor": 1.5},
    "FINAL": {"iterations": 6, "mesh_quality": "SMOOTH", "mesh_cell_relative": True, "mesh_cell_factor": 1.0},
}


class LIQUID_OT_quality(bpy.types.Operator):
    bl_idname = "fumaris_liquid.quality"
    bl_label = "Set Liquid Quality"
    bl_description = ("Set the settings that trade speed for quality in one step. Draft: 2 pressure iterations, "
                      "surface built on the GPU. Final: 6 iterations, Smooth mesh. "
                      "Each setting stays editable afterwards")
    bl_options = {"REGISTER", "UNDO"}
    level: bpy.props.EnumProperty(items=[("DRAFT", "Draft", "Fast to simulate and mesh, softer splashes"),
                                         ("FINAL", "Final", "Full pressure solve and the smooth surface")])

    def execute(self, context):
        domain = domain_for(context)
        if domain is None:
            self.report({"ERROR"}, "Select a liquid domain")
            return {"CANCELLED"}
        if session.active() is not None:
            self.report({"ERROR"}, "Stop the simulation before changing quality")
            return {"CANCELLED"}
        for name, value in QUALITY_LEVELS[self.level].items():
            setattr(domain.fumaris_liquid, name, value)
        self.report({"INFO"}, f"{self.level.title()} quality set")
        return {"FINISHED"}


class LIQUID_OT_diagnostics(bpy.types.Operator):
    bl_idname = "fumaris_liquid.copy_diagnostics"
    bl_label = "Copy Diagnostics"
    bl_description = "Copy version, worker, settings and current error to the clipboard"

    def execute(self, context):
        from .exporters import settings
        domain = domain_for(context)
        from . import bl_info
        details = {"addon": "Aquaris " + ".".join(map(str, bl_info["version"])), "blender": bpy.app.version_string,
                   "platform": platform.platform()}
        if domain is not None:
            p = domain.fumaris_liquid
            details.update({"domain": domain.name, "status": p.status,
                            "runtime": p.runtime_info, "error": p.last_error,
                            "warning": p.runtime_warning})
            try:
                details["settings"] = settings(context.scene, domain)
            except Exception as exc:
                details["settings_error"] = str(exc)
        context.window_manager.clipboard = json.dumps(details, indent=2, ensure_ascii=False)
        self.report({"INFO"}, "Liquid diagnostics copied to clipboard")
        return {"FINISHED"}


classes = (LIQUID_OT_setup, LIQUID_OT_control, LIQUID_OT_cache, LIQUID_OT_export_alembic, LIQUID_OT_quality, LIQUID_OT_diagnostics)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
