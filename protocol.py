"""Small binary transport shared by Blender and the headless liquid worker.

JSON describes each contiguous little-endian array; particle arrays never pass
through JSON.  No Blender imports: this module also runs in standalone tests.
"""
import array
import json
import struct
import sys

MAGIC = b"FLQ1"
ENVELOPE = struct.Struct("<4sIQ")
MAX_HEADER = 1024 * 1024
MAX_PAYLOAD = 1536 * 1024 * 1024
VEC3_FIELDS = frozenset({"positions", "velocities", "boundary_positions",
                        "boundary_velocities", "boundary_normals", "vertices", "vertex_velocities", "whitewater_positions",
                        "whitewater_velocities"})
UINT3_FIELDS = frozenset({"triangles"})
UINT_FIELDS = frozenset({"whitewater_types"})


def _read_exact(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        block = stream.read(size - len(chunks))
        if not block:
            raise EOFError("Liquid worker closed its output stream")
        chunks.extend(block)
    return bytes(chunks)


def write_message(stream, header, payload=b""):
    encoded = json.dumps(header, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_HEADER or len(payload) > MAX_PAYLOAD:
        raise ValueError("Liquid message exceeds the transport budget")
    # Unbuffered process pipes may legally return a short write.
    for data in (ENVELOPE.pack(MAGIC, len(encoded), len(payload)), encoded, payload):
        remaining = memoryview(data)
        while remaining:
            count = stream.write(remaining)
            if not count:
                raise OSError("Liquid worker input stream closed during write")
            remaining = remaining[count:]
    stream.flush()


def read_message(stream):
    magic, header_size, payload_size = ENVELOPE.unpack(_read_exact(stream, ENVELOPE.size))
    if magic != MAGIC:
        raise ValueError("Liquid worker returned an incompatible protocol")
    if header_size > MAX_HEADER or payload_size > MAX_PAYLOAD:
        raise ValueError("Liquid message exceeds the transport budget")
    header = json.loads(_read_exact(stream, header_size))
    if not isinstance(header, dict):
        raise ValueError("Liquid message header must be an object")
    payload = _read_exact(stream, payload_size)
    validate_blocks(header, payload)
    return header, payload


def validate_blocks(header, payload):
    occupied = []
    for field, block in header.get("blocks", {}).items():
        if field in VEC3_FIELDS or field in UINT3_FIELDS:
            stride = 12
        elif field in UINT_FIELDS:
            stride = 4
        else:
            raise ValueError("Unknown liquid array: " + field)
        offset, count = block["offset"], block["count"]
        if (type(offset) is not int or type(count) is not int or
                offset < 0 or count < 0 or offset % 4 or offset + count * stride > len(payload)):
            raise ValueError("Invalid liquid array extent: " + field)
        if count:
            occupied.append((offset, offset + count * stride))
    occupied.sort()
    if any(a[1] > b[0] for a, b in zip(occupied, occupied[1:])):
        raise ValueError("Liquid array extents overlap")


def pack_arrays(**fields):
    """Return (blocks, payload) for flat buffers or iterables of 3-tuples."""
    payload = bytearray()
    blocks = {}
    for name, values in fields.items():
        if name in VEC3_FIELDS:
            dtype, width = "f", 3
        elif name in UINT3_FIELDS:
            dtype, width = "I", 3
        elif name in UINT_FIELDS:
            dtype, width = "I", 1
        else:
            raise ValueError("Unknown liquid array: " + name)
        if isinstance(values, (bytes, bytearray, memoryview)):
            data = bytes(values)
            if len(data) % (4 * width):
                raise ValueError("Invalid array byte length: " + name)
        else:
            buffer = array.array(dtype)
            for value in values:
                if width == 1 or isinstance(value, (int, float)):
                    buffer.append(value)
                else:
                    buffer.extend(value)
            if len(buffer) % width:
                raise ValueError("Invalid array element count: " + name)
            if buffer.itemsize != 4:
                raise RuntimeError("Liquid arrays require 32-bit native scalars")
            if sys.byteorder != "little":
                buffer.byteswap()
            data = buffer.tobytes()
        blocks[name] = {"offset": len(payload), "count": len(data) // (4 * width)}
        payload.extend(data)
    return blocks, bytes(payload)


def unpack_array(header, payload, name):
    block = header.get("blocks", {}).get(name, {"offset": 0, "count": 0})
    width = 1 if name in UINT_FIELDS else 3
    dtype = "f" if name in VEC3_FIELDS else "I"
    result = array.array(dtype)
    start = block["offset"]
    result.frombytes(payload[start:start + block["count"] * width * 4])
    if sys.byteorder != "little":
        result.byteswap()
    return result
