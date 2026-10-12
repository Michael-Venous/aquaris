"""Fumaris-style participant collections, transport and bake controls."""

import bpy
import textwrap

from . import operators, session


# The one line the panel shows for what this version does not simulate.
UNAVAILABLE = "Viscosity, surface tension, whitewater and force fields are not available in this version."


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
    box = _section(layout, "participant", "Participant: " + obj.name)
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
        box.label(text="Force fields are not available in this version", icon="INFO")
        box.label(text="Disable or remove it to simulate")
    elif p.role == "OUTFLOW":
        box.label(text="Removes particles inside the world bounding box")
    elif p.role == "COLLIDER":
        box.label(text="Evaluated mesh surface; animation supported")



def _transport(layout, context, domain, compact=False):
    p = domain.fumaris_liquid
    running = session.active()
    owned = running is not None and running.domain == domain
    box = layout.box()
    box.label(text=domain.name, icon="MOD_FLUIDSIM")
    width = max(24, int(context.region.width / 7) - 8) if context.region else 48
    for line in textwrap.wrap(p.status, width=width):
        box.label(text=line)
    if not compact:
        quality = box.row(align=True)
        quality.enabled = running is None
        current = {name: getattr(p, name) for name in operators.QUALITY_LEVELS["DRAFT"]}
        for level, label in (("DRAFT", "Draft"), ("FINAL", "Final")):
            match = all(abs(current[name] - value) < 1e-6 if isinstance(value, float) else current[name] == value
                        for name, value in operators.QUALITY_LEVELS[level].items())
            quality.operator("fumaris_liquid.quality", text=label, depress=match).level = level
        box.operator("fumaris_liquid.copy_diagnostics", text="Copy Diagnostics", icon="COPYDOWN")
    if p.last_error:
        error = box.column()
        error.alert = True
        for line in textwrap.wrap(p.last_error, width=width)[:6]:
            error.label(text=line, icon="ERROR")
    if p.runtime_warning:
        warning = box.column()
        warning.alert = True
        for line in textwrap.wrap(p.runtime_warning, width=width):
            warning.label(text=line, icon="INFO")
    row = box.row(align=True)
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
        _participant(layout, context)
        _transport(layout, context, domain, compact=True)
        return
    if domain is not None:
        p = domain.fumaris_liquid
        running = session.active()
        _transport(layout, context, domain)
        box = _section(layout, "collections", "Participant Collections", closed=True)
        if box is not None:
            collections = box.column()
            collections.enabled = running is None
            for name in ("emitter_collection", "collider_collection", "effector_collection", "outflow_collection"):
                collections.prop(p, name)
        box = _section(layout, "simulation", "Simulation", closed=True)
        if box is not None:
            structural = box.column()
            structural.enabled = running is None
            structural.prop(p, "particle_radius")
            for name in ("substeps", "step_size", "speed", "gravity", "flip_ratio"):
                box.prop(p, name)
            note = box.column(align=True)
            for line in textwrap.wrap(UNAVAILABLE, width=max(24, int(context.region.width / 7) - 8) if context.region else 48):
                note.label(text=line)
        box = _section(layout, "whitewater", "Whitewater from Earlier Caches", closed=True)
        if box is not None:
            # Nothing here simulates: these show the foam, spray and bubbles a
            # cache baked by an earlier version contains.
            box.prop(p, "whitewater")
            look = box.column(align=True)
            look.enabled = p.whitewater
            look.prop(p, "whitewater_size")
            look.prop(p, "whitewater_mist")
            mist = look.row()
            mist.enabled = p.whitewater_mist > 0
            mist.prop(p, "whitewater_mist_radius")
        box = _section(layout, "budgets", "Allocation and Budgets", closed=True)
        if box is not None:
            box.enabled = running is None
            box.prop(p, "auto_budgets")
            manual = box.column()
            manual.enabled = not p.auto_budgets
            manual.prop(p, "max_particles")
            manual.prop(p, "max_boundary")
            box.label(text="Initial GPU region (auto expands)")
            box.prop(p, "bounds_min")
            box.prop(p, "bounds_max")
            box.prop(p, "remove_strays")
        box = _section(layout, "display", "Surface and Display", closed=True)
        if box is not None:
            structural = box.column()
            # Applied to the next frame of a run, and at once to the held frame
            # of a paused one; fixed during a bake so its frames stay alike.
            structural.enabled = running is None or not running.bake
            structural.prop(p, "mesh_cell_relative")
            structural.prop(p, "mesh_cell_factor" if p.mesh_cell_relative else "mesh_cell_size")
            cells = structural.row()
            cells.enabled = not p.auto_budgets
            cells.prop(p, "max_mesh_cells")
            structural.prop(p, "mesh_quality")
            structural.prop(p, "droplet_scale")
            structural.prop(p, "surface_smoothing")
            structural.prop(p, "surface_velocity")
            box.prop(p, "display")
            if p.display == "PREVIEW":
                box.label(text="GPU Surface preview; bake for a renderable mesh")
            else:
                box.label(text="Water/whitewater materials and nodes are editable")
        box = _section(layout, "cache", "Recording and Cache", closed=True)
        if box is not None:
            editable = box.column()
            editable.enabled = running is None
            for name in ("record_preview", "cache_directory", "cache_slot", "bake_start", "bake_end", "cache_playback"):
                editable.prop(p, name)
            row = editable.row(align=True)
            row.operator("fumaris_liquid.cache", text="Load Frame", icon="IMPORT").action = "LOAD"
            row.operator("fumaris_liquid.cache", text="Delete Slot", icon="TRASH").action = "DELETE"
            editable.operator("fumaris_liquid.export_alembic", icon="EXPORT")


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


classes = (LIQUID_PT_physics, LIQUID_PT_sidebar)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
