"""Fumaris-style participant collections, transport and bake controls."""

import json
import os
import textwrap

import bpy

from . import exporters, operators, session

_ROLE_ICONS = {"EMITTER": "OUTLINER_OB_FORCE_FIELD", "COLLIDER": "MOD_PHYSICS", "EFFECTOR": "FORCE_FORCE",
               "OUTFLOW": "MOD_FLUID", "DOMAIN": "MOD_FLUIDSIM"}
_slot_summaries = {}  # slot folder -> (folder's modification time, summary): the panel redraws far more often than a bake writes


def _slot_summary(domain):
    """'N frames (a–b)', 'empty', and whether the slot is protected, for the cache section."""
    try:
        cache = session.cache_for(domain)
        stamp = os.stat(cache.path).st_mtime_ns if cache.path.exists() else 0
        known = _slot_summaries.get(str(cache.path))
        if known is not None and known[0] == stamp:
            return known[1]
        frames = cache.frames()
        summary = (f"{len(frames)} frames ({frames[0]}–{frames[-1]})" if frames else "empty", cache.protected())
    except Exception:
        summary = ("not readable", False)
        stamp = None
    if stamp is not None:
        _slot_summaries[str(cache.path)] = (stamp, summary)
    return summary


def _wrapped(layout, text, width, icon="NONE", alert=False, lines=6):
    column = layout.column(align=True)
    column.alert = alert
    for index, line in enumerate(textwrap.wrap(text, width=width)[:lines]):
        column.label(text=line, icon=icon if index == 0 else "BLANK1")


def _control(layout, text, action, icon):
    op = layout.operator("fumaris_liquid.control", text=text, icon=icon)
    op.action = action


def _section(layout, key, title, closed=False):
    header, body = layout.panel("liquid_" + key, default_closed=closed)
    header.label(text=title)
    return body


def _participant(layout, context):
    obj = context.object
    if obj is None or obj.get("fumaris_liquid_output") or obj.fumaris_liquid.role == "DOMAIN":
        return
    p = obj.fumaris_liquid
    header, box = layout.panel("liquid_participant", default_closed=False)
    header.label(text=p.bl_rna.properties["role"].enum_items[p.role].name + ": " + obj.name, icon=_ROLE_ICONS.get(p.role, "OBJECT_DATA"))
    if box is None:
        return
    box.prop(p, "enabled")
    if p.role == "EMITTER":
        for name in ("source_shape", "emission_mode"):
            box.prop(p, name)
        if p.source_shape == "COLLECTION":
            box.prop(p, "source_collection")
        if p.source_shape == "VOLUME":
            for name in ("volume_grid", "volume_threshold", "volume_resolution"):
                box.prop(p, name)
            if p.volume_resolution == "VOXEL_SIZE":
                box.prop(p, "volume_voxel_size")
        if p.emission_mode == "CONTINUOUS":
            box.prop(p, "emission_frequency")
        for name in ("velocity", "inherited_motion"):
            box.prop(p, name)
        if p.source_shape not in {"POINTS", "PARTICLES"}:
            box.prop(p, "normal_velocity")
        mask = _section(layout, "source_masks", "Source Masks and Attributes", closed=True)
        if mask is not None:
            if p.source_shape not in {"PARTICLES", "VOLUME"}:
                for name in ("mask_attribute", "mask_threshold", "velocity_attribute"):
                    mask.prop(p, name)
            mask.prop(p, "mask_type")
            if p.mask_type != "NONE":
                mask.prop(p, "mask_texture" if p.mask_type == "TEXTURE" else "mask_image")
                mask.prop(p, "mask_coordinates")
                if p.mask_type == "IMAGE":
                    if p.mask_image is not None and p.mask_image.source == "SEQUENCE":
                        mask.prop(p, "mask_image_offset")
                    mask.prop(p, "mask_projection")
                for name in ("mask_scale", "mask_offset", "mask_threshold"):
                    mask.prop(p, name)
    elif p.role == "EFFECTOR":
        box.prop(p, "effector_kind")
        if p.effector_kind == "FORCE":
            box.prop(p, "force_vector")
        box.prop(p, "force_strength")
        box.prop(p, "force_radius")
    elif p.role == "OUTFLOW":
        box.label(text="Removes particles inside the world bounding box")
    elif p.role == "COLLIDER":
        box.prop(p, "collider_margin")
        box.label(text="Evaluated mesh surface; animation supported")



def _transport(layout, context, domain, compact=False):
    """The Simulate block, always open: play and bake, status, progress and the settings every run starts from."""
    p = domain.fumaris_liquid
    running = session.active()
    owned = running is not None and running.domain == domain
    width = max(24, int(context.region.width / 7) - 8) if context.region else 48
    box = layout.box()
    box.label(text=domain.name, icon="MOD_FLUIDSIM")
    if owned:
        icon = "RENDER_ANIMATION" if running.bake else "PAUSE" if running.paused else "PLAY"
    else:
        icon = "ERROR" if p.last_error else "CHECKMARK" if p.status.startswith("Bake complete") else "INFO"
    _wrapped(box, p.status, width, icon=icon)
    if p.progress:
        _wrapped(box, p.progress, width, icon="TIME")
    elif p.bake_summary and not owned:
        _wrapped(box, p.bake_summary, width, icon="FILE_TICK", lines=2)
    if p.last_error:
        _wrapped(box, p.last_error + " (details: Diagnostics, at the bottom)", width, icon="ERROR", alert=True)
    if p.runtime_warning:
        _wrapped(box, p.runtime_warning, width, icon="INFO", alert=True)
    row = box.row(align=True)
    row.scale_y = 1.2
    if owned:
        if running.bake:
            _control(row, "Cancel Bake", "CANCEL", "CANCEL")
        else:
            _control(row, "Resume" if running.paused else "Pause",
                     "RESUME" if running.paused else "PAUSE", "PLAY" if running.paused else "PAUSE")
            _control(row, "Stop", "STOP", "SNAP_FACE")
    else:
        row.enabled = running is None
        _control(row, "Play", "PLAY", "PLAY")
        if not compact:
            _control(row, "Bake", "BAKE", "RENDER_ANIMATION")
            _control(row, "Reset", "RESET", "FILE_REFRESH")
    if compact:
        return
    quality = box.row(align=True)
    quality.enabled = running is None
    current = {name: getattr(p, name) for name in operators.QUALITY_LEVELS["DRAFT"]}
    for level, label in (("DRAFT", "Draft"), ("FINAL", "Final")):
        match = all(abs(current[name] - value) < 1e-6 if isinstance(value, float) else current[name] == value
                    for name, value in operators.QUALITY_LEVELS[level].items())
        quality.operator("fumaris_liquid.quality", text=label, depress=match).level = level
    settings = box.column()
    fixed = settings.column()
    fixed.enabled = running is None
    fixed.prop(p, "particle_radius")
    frames = fixed.row(align=True)
    frames.use_property_split = False  # one row as in Fumaris: the clock, then Warmup or Start and End
    frames.use_property_decorate = False
    frames.prop(p, "use_scene_frame_range", text="", icon="TIME")
    if p.use_scene_frame_range:
        frames.prop(p, "warmup_frames")
    else:
        frames.prop(p, "bake_start", text="Start")
        frames.prop(p, "bake_end", text="End")
    if running is not None:
        settings.label(text="Stop the simulation to change radius or range", icon="INFO")
    record = settings.row()
    record.enabled = running is None
    record.prop(p, "record_preview")
    settings.prop(p, "preview_rate")


def _diagnostics(layout, context, domain):
    p = domain.fumaris_liquid
    running = session.active()
    width = max(24, int(context.region.width / 7) - 8) if context.region else 48
    try:
        info = json.loads(p.runtime_info) if p.runtime_info else {}
    except ValueError:
        info = {}
    column = layout.column(align=True)
    column.label(text="GPU: " + (info.get("device") or "not started yet"), icon="MEMORY")
    response = running.last_response if running is not None and running.domain == domain else None
    if response:
        parts = []
        for key, label in (("simulation_ms", "simulate"), ("meshing_ms", "mesh"), ("worker_ms", "frame")):
            if isinstance(response.get(key), (int, float)):
                parts.append(f"{label} {response[key]:.1f} ms")
        if parts:
            column.label(text="Last frame: " + ", ".join(parts), icon="TIME")
        particles = response.get("particle_count", 0)
        limit = exporters.limits(p)[0]
        share = particles / limit if limit else 0
        column.label(text=f"Particles: {particles:,} of {limit:,} ({share:.0%})", icon="ERROR" if share > 0.9 else "PARTICLES")
        if share > 0.9:
            _wrapped(column, "Almost at the particle budget: emitters stop adding liquid there. Raise Particle Budget "
                             "(Advanced) or turn on Automatic Limits", width, alert=True)
        whitewater = (response.get("whitewater") or {}).get("count")
        if isinstance(whitewater, int) and p.whitewater_generate:
            column.label(text=f"Whitewater: {whitewater:,} of {p.max_whitewater:,}")
    elif running is None:
        column.label(text="Play or bake to see live figures", icon="INFO")
    if p.last_error:
        _wrapped(layout, p.last_error, width, icon="ERROR", alert=True, lines=10)
    layout.operator("fumaris_liquid.copy_diagnostics", text="Copy Diagnostics", icon="COPYDOWN")


def _draw(layout, context):
    obj = context.object
    if obj is None:
        return
    layout.use_property_split = True
    layout.use_property_decorate = True
    p = obj.fumaris_liquid
    domain = operators.domain_for(context)
    is_output = bool(obj.get("fumaris_liquid_output"))
    header = layout.box()
    header.label(text=obj.name, icon="OBJECT_DATA")
    if is_output:
        header.label(text="Generated Aquaris Output", icon="MOD_FLUIDSIM")
    else:
        role_row = header.row()
        running = session.active()
        role_row.enabled = running is None
        role_row.prop(p, "role")
    if not is_output and p.role == "NONE":
        setup = header.row()
        setup.enabled = session.active() is None
        setup.operator("fumaris_liquid.quick_setup", text="Quick Setup…", icon="ADD")
        return
    if domain is None:
        _participant(layout, context)
        if is_output:
            layout.label(text="The output's Aquaris domain is missing", icon="INFO")
        else:
            layout.label(text="Assign this object to a domain's participant collection", icon="INFO")
        return
    if p.role != "DOMAIN" and not is_output:
        _transport(layout, context, domain, compact=True)
        _participant(layout, context)
        return
    if domain is not None:
        p = domain.fumaris_liquid
        running = session.active()
        _transport(layout, context, domain)
        box = _section(layout, "collections", "Participant Collections")
        if box is not None:
            collections = box.column()
            collections.enabled = running is None
            for name in ("emitter_collection", "collider_collection", "effector_collection", "outflow_collection"):
                collections.prop(p, name)
        box = _section(layout, "simulation", "Liquid", closed=True)
        if box is not None:
            for name in ("substeps", "step_size", "collider_step", "speed", "gravity", "flip_ratio"):
                box.prop(p, name)
            box.prop(p, "viscosity_preset")
            row = box.row()
            row.enabled = p.viscosity_preset == "CUSTOM"
            row.prop(p, "viscosity")
            box.prop(p, "surface_tension")
        box = _section(layout, "whitewater", "Whitewater", closed=True)
        if box is not None:
            box.prop(p, "whitewater_generate")
            generation = box.column(align=True)
            generation.enabled = p.whitewater_generate
            for name in ("whitewater_amount", "whitewater_lifetime", "whitewater_buoyancy", "whitewater_drag", "max_whitewater"):
                generation.prop(p, name)
            box.prop(p, "whitewater")
            look = box.column(align=True)
            look.enabled = p.whitewater
            look.prop(p, "whitewater_size")
            look.prop(p, "whitewater_mist")
            mist = look.row()
            mist.enabled = p.whitewater_mist > 0
            mist.prop(p, "whitewater_mist_radius")
        box = _section(layout, "display", "Surface and Display", closed=True)
        if box is not None:
            box.prop(p, "display")
            if p.display == "PREVIEW":
                box.label(text="GPU Surface is a fast preview; bake or use Surface to render", icon="INFO")
            else:
                box.label(text="Water and whitewater materials and nodes are editable", icon="INFO")
            structural = box.column()
            # Applied to the next frame of a run, and at once to the held frame
            # of a paused one; fixed during a bake so its frames stay alike.
            structural.enabled = running is None or not running.bake
            structural.prop(p, "mesh_quality")
            structural.prop(p, "surface_smoothing")
            structural.prop(p, "mesh_cell_relative")
            structural.prop(p, "mesh_cell_factor" if p.mesh_cell_relative else "mesh_cell_size")
            structural.prop(p, "surface_velocity")
            if running is not None and running.bake:
                box.label(text="Surface settings are fixed during a bake", icon="INFO")
        box = _section(layout, "cache", "Cache and Export", closed=True)
        if box is not None:
            editable = box.column()
            editable.enabled = running is None
            editable.prop(p, "cache_directory")
            summary, protected = _slot_summary(domain)
            slot = editable.row(align=True)
            slot.prop(p, "cache_slot")
            slot.operator("fumaris_liquid.cache_slot_new", text="", icon="ADD")
            slot.operator("fumaris_liquid.cache_protect", text="", icon="LOCKED" if protected else "UNLOCKED", depress=protected)
            editable.label(text=f"Slot '{p.cache_slot}': {summary}" + (" · protected" if protected else ""),
                           icon="LOCKED" if protected else "DISK_DRIVE")
            editable.prop(p, "cache_playback")
            row = editable.row(align=True)
            row.operator("fumaris_liquid.cache", text="Load Frame", icon="IMPORT").action = "LOAD"
            delete = row.row(align=True)
            delete.enabled = not protected
            delete.operator("fumaris_liquid.cache", text="Delete Slot", icon="TRASH").action = "DELETE"
            box.operator("fumaris_liquid.rebuild_surface", icon="MOD_REMESH")
            editable.operator("fumaris_liquid.export_alembic", icon="EXPORT")
            editable.operator("fumaris_liquid.export_vdb", icon="VOLUME_DATA")
        box = _section(layout, "budgets", "Advanced", closed=True)
        if box is not None:
            box.enabled = running is None
            box.label(text="Region (grows by itself to follow the liquid)")
            box.prop(p, "bounds_min")
            box.prop(p, "bounds_max")
            box.label(text="Walls at the region's sides")
            box.row(align=True).prop(p, "domain_walls")
            box.prop(p, "remove_strays")
            box.prop(p, "auto_budgets")
            manual = box.column()
            manual.enabled = not p.auto_budgets
            manual.prop(p, "max_particles")
            manual.prop(p, "max_boundary")
            manual.prop(p, "max_mesh_cells")
            if running is not None:
                box.label(text="Stop the simulation to change these", icon="INFO")
        box = _section(layout, "diagnostics", "Diagnostics", closed=True)
        if box is not None:
            _diagnostics(box, context, domain)


class LIQUID_PT_physics(bpy.types.Panel):
    bl_label = "Aquaris"
    bl_idname = "LIQUID_PT_physics"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "physics"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        _draw(self.layout, context)


class LIQUID_PT_sidebar(bpy.types.Panel):
    bl_label = "Aquaris"
    bl_idname = "LIQUID_PT_sidebar"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Aquaris"

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        _draw(self.layout, context)


class LIQUID_MT_add(bpy.types.Menu):
    """Add > Aquaris Liquid: each Quick Setup at the 3D cursor, without the dialog."""
    bl_idname = "LIQUID_MT_add"
    bl_label = "Aquaris Liquid"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = "EXEC_DEFAULT"
        layout.operator_enum("fumaris_liquid.quick_setup", "preset")


def _add_menu(self, context):
    self.layout.menu("LIQUID_MT_add", icon="MOD_FLUIDSIM")


classes = (LIQUID_PT_physics, LIQUID_PT_sidebar, LIQUID_MT_add)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_add.append(_add_menu)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(_add_menu)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
