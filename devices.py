"""Discovery and UUID-backed dropdown state. Enumeration never starts a simulation."""
import json
import subprocess

_devices = []
_loaded = False
_error = ""
# Blender keeps references to strings from dynamic EnumProperty callbacks.
_items = {}


def refresh(executable):
    global _devices, _loaded, _error
    try:
        result = subprocess.run([str(executable), "--list-gpus"], capture_output=True,
                                text=True, timeout=10, check=True)
        data = json.loads(result.stdout)
        if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("devices"), list):
            raise ValueError("Worker does not support GPU discovery; update the Aquaris worker")
        devices = data["devices"]
        for device in devices:
            if not isinstance(device, dict) or not isinstance(device.get("uuid"), str) or not isinstance(device.get("name"), str):
                raise ValueError("Worker returned an invalid GPU identity")
        _devices = [d for d in devices if d.get("simulation_supported") and not d.get("software")]
        _loaded, _error = True, ""
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        _devices, _loaded = [], False
        detail = (getattr(exc, "stderr", "") or str(exc)).strip()
        _error = "Could not list GPUs: " + detail[:400]
        raise RuntimeError(_error) from exc
    return _devices


def items(preferences, context=None):
    saved = preferences.simulation_gpu_uuid
    label = preferences.simulation_gpu_label
    rows = [("NONE", "Choose a GPU" if _loaded else "Refresh GPUs to choose", "", 0)]
    for d in _devices:
        rows.append((d["uuid"], d["name"] + " [" + d["uuid"][:8] + "]",
                     "Vulkan device " + d["uuid"] + "; applies to the next preview or bake", len(rows)))
    if saved and not any(r[0] == saved for r in rows):
        rows.append((saved, ("Unavailable: " if _loaded else "Saved: ") + (label or saved),
                     "Refresh GPUs to verify availability. A missing selection causes an error, never a silent switch", len(rows)))
    key = tuple(rows)
    return _items.setdefault(key, rows)


def get_selection(preferences):
    return next((r[3] for r in items(preferences) if r[0] == preferences.simulation_gpu_uuid), 0)


def set_selection(preferences, value):
    row = next((r for r in items(preferences) if r[3] == value), None)
    if row is None:
        return
    preferences.simulation_gpu_uuid = "" if row[0] == "NONE" else row[0]
    preferences.simulation_gpu_label = next((d["name"] for d in _devices if d["uuid"] == row[0]),
                                          preferences.simulation_gpu_label)
