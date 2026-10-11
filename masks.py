"""Explicit world/object/bounds texture coordinates for particle emission masks."""

import numpy as np


def active(props, positions, geometry=None, matrix=None, image_cache=None):
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
        if image.source not in {"FILE", "GENERATED"}:
            raise RuntimeError("Image masks support static FILE or GENERATED images; movie, sequence and UDIM images require a single-frame image")
        width, height = image.size
        if width <= 0 or height <= 0 or len(image.pixels) != width * height * 4:
            raise RuntimeError("The mask image has no readable RGBA pixel data")
        key = image.as_pointer()
        pixels = image_cache.get(key) if image_cache is not None else None
        if pixels is None:
            pixels = np.empty(width * height * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            pixels = pixels.reshape((height, width, 4))
            if image_cache is not None:
                image_cache[key] = pixels
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
