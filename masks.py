"""Explicit world/object/bounds texture coordinates for particle emission masks."""

import os
from pathlib import Path
import re

import numpy as np


_loaders = {}


def clear():
    """Remove the temporary images used to read sequence files."""
    import bpy
    for name in list(_loaders.values()):
        image = bpy.data.images.get(name)
        if image is not None:
            bpy.data.images.remove(image)
    _loaders.clear()


def _pixels(image, label=None):
    width, height = image.size
    if width <= 0 or height <= 0 or len(image.pixels) != width * height * 4:
        raise RuntimeError("The mask image has no readable RGBA pixel data" + (": " + label if label else ""))
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape((height, width, 4))


def _sequence_pixels(image, number, cache):
    """RGBA of the sequence file for a file number, holding the first and last files.

    A sequence image exposes no pixels of its own; the numbered file is read
    through a temporary image with the sequence's color settings. clear()
    removes those temporary images when the session ends.
    """
    import bpy
    first = Path(bpy.path.abspath(image.filepath, library=image.library))
    digits = re.search(r"[0-9]+(?=[^0-9]*$)", first.stem)
    if digits is None:
        raise RuntimeError("The mask image sequence file name has no frame number: " + first.name)
    head, tail = first.stem[:digits.start()], first.stem[digits.end():] + first.suffix
    width = len(digits.group())
    listing = ("sequence", str(first.parent), head, tail, width)
    numbers = cache.get(listing) if cache is not None else None
    if numbers is None:
        # Case follows the filesystem's own rules, as Blender's file lookup does.
        fold = os.path.normcase
        pattern = re.compile(re.escape(fold(head)) + r"([0-9]+)" + re.escape(fold(tail)) + r"\Z")
        try:
            names = [fold(entry.name) for entry in first.parent.iterdir()]
        except OSError as exc:
            raise RuntimeError("The mask image sequence folder cannot be read: " + str(first.parent)) from exc
        # Only names Blender itself would generate for this sequence.
        numbers = sorted({int(match.group(1)) for match in map(pattern.match, names)
                          if match and match.group() == fold(f"{head}{int(match.group(1)):0{width}d}{tail}")})
        if cache is not None:
            cache[listing] = numbers
    if not numbers:
        raise RuntimeError("The mask image sequence has no files: " + str(first))
    number = min(max(number, numbers[0]), numbers[-1])
    path = first.parent / f"{head}{number:0{width}d}{tail}"
    if number not in numbers:
        raise RuntimeError("The mask image sequence is missing " + path.name)
    key = ("sequence frame", str(path))
    pixels = cache.get(key) if cache is not None else None
    if pixels is None:
        # One reusable temporary image per sequence: adding and removing a
        # datablock every frame would force a dependency-graph rebuild each time.
        loaded = bpy.data.images.get(_loaders.get(image.name_full, ""))
        try:
            if loaded is None:
                loaded = bpy.data.images.load(str(path), check_existing=False)
                loaded.name = ".aquaris mask sequence"
                _loaders[image.name_full] = loaded.name
            else:
                loaded.filepath = str(path)
                loaded.reload()
        except RuntimeError as exc:
            raise RuntimeError("The mask image sequence file cannot be loaded: " + path.name) from exc
        loaded.colorspace_settings.name = image.colorspace_settings.name
        loaded.alpha_mode = image.alpha_mode
        pixels = _pixels(loaded, path.name)
        if cache is not None:
            cache[key] = pixels
    return pixels


def active(props, positions, geometry=None, matrix=None, image_cache=None, frame=None):
    if props.mask_type == "NONE" or not len(positions):
        return np.ones(len(positions), dtype=bool)
    if matrix is None:
        matrix = geometry[2] if geometry is not None else np.eye(4)
    if props.mask_coordinates == "WORLD":
        coordinates = np.array(positions, dtype=np.float64, copy=True)
    else:
        inverse = np.linalg.inv(matrix)
        coordinates = (positions - matrix[:3, 3]) @ inverse[:3, :3].T
        if props.mask_coordinates == "BOUNDS":
            bounds = geometry[0] if geometry is not None else positions
            bounds = (bounds - matrix[:3, 3]) @ inverse[:3, :3].T
            minimum, maximum = bounds.min(axis=0), bounds.max(axis=0)
            coordinates = (coordinates - minimum) / np.maximum(maximum - minimum, 1e-12)
    coordinates = coordinates * np.asarray(props.mask_scale) + np.asarray(props.mask_offset)
    if props.mask_type == "TEXTURE":
        texture = props.mask_texture
        if texture is None:
            raise RuntimeError("Assign a Blender Texture for this emitter's texture mask")
        if texture.use_nodes or texture.type == "IMAGE":
            raise RuntimeError("Texture masks use native procedural Texture datablocks; select Image mode for images")
        # Native procedural textures take coordinates in -1..1 for normalized
        # bounds, and expose scalar intensity in the fourth evaluate component.
        if props.mask_coordinates == "BOUNDS":
            coordinates = coordinates * 2 - 1
        values = np.asarray([texture.evaluate(tuple(point))[3] for point in coordinates])
    else:
        image = props.mask_image
        if image is None:
            raise RuntimeError("Assign an Image for this emitter's image mask")
        if image.source == "SEQUENCE":
            if frame is None:
                raise RuntimeError("Image sequence masks need the simulation frame")
            pixels = _sequence_pixels(image, int(frame) + props.mask_image_offset, image_cache)
        elif image.source in {"FILE", "GENERATED"}:
            key = image.as_pointer()
            pixels = image_cache.get(key) if image_cache is not None else None
            if pixels is None:
                pixels = _pixels(image)
                if image_cache is not None:
                    image_cache[key] = pixels
        else:
            raise RuntimeError("Image masks support FILE, GENERATED and SEQUENCE images; movie and UDIM images require image files")
        height, width = pixels.shape[:2]
        axes = {"XY": (0, 1), "XZ": (0, 2), "YZ": (1, 2)}[props.mask_projection]
        uv = coordinates[:, axes]
        inside = np.all((uv >= 0) & (uv <= 1), axis=1)
        xy = np.clip(uv, 0, 1) * np.asarray((width - 1, height - 1))
        low = np.floor(xy).astype(np.int32)
        high = np.minimum(low + 1, (width - 1, height - 1))
        factor = xy - low
        left = pixels[low[:, 1], low[:, 0]] * (1 - factor[:, 1, None]) + pixels[high[:, 1], low[:, 0]] * factor[:, 1, None]
        right = pixels[low[:, 1], high[:, 0]] * (1 - factor[:, 1, None]) + pixels[high[:, 1], high[:, 0]] * factor[:, 1, None]
        rgba = left * (1 - factor[:, 0, None]) + right * factor[:, 0, None]
        values = (rgba[:, :3] @ np.asarray((0.2126, 0.7152, 0.0722))) * rgba[:, 3]
        values[~inside] = np.nan
    return np.isfinite(values) & (values >= props.mask_threshold)
