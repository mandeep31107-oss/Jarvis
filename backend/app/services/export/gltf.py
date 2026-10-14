"""glTF 2.0 binary (.glb) export of the real model geometry.

Every wall, slab, door, window, stair flight, roof plane, furniture item and site
element becomes an actual box in the glTF scene graph (no placeholders). Boxes are
grouped into one mesh primitive set per material so viewers can render with a small
number of materials. Units are metres, +x East, +y North (glTF is right handed with
+y up, so plan +y maps to glTF -z).
"""

from __future__ import annotations

import json
import math
import struct
from typing import Dict, List, Optional, Tuple

from ...schemas import Floor, Opening, ProjectData, Room
from ..geometry import WallSegment, derive_walls
from ..room_catalog import ROOM_LABELS

SLAB = 0.15
GROUP_ORDER = ["structure", "exterior_wall", "interior_wall", "door", "window", "glass",
               "stair", "roof", "floor_finish", "furniture", "fixture", "site", "landscape"]


def _plan_to_gltf(x: float, y: float, z: float) -> Tuple[float, float, float]:
    return (x, z, -y)


class _GlbBuilder:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.buffer_views: List[dict] = []
        self.accessors: List[dict] = []
        self.meshes: List[dict] = []
        self.nodes: List[dict] = []
        self.materials: List[dict] = []
        self._material_index: Dict[str, int] = {}

    # -- materials ---------------------------------------------------------
    def material(self, name: str, color: Tuple[float, float, float],
                 roughness: float = 0.85, metal: float = 0.0,
                 alpha: float = 1.0, double_sided: bool = False) -> int:
        key = f"{name}|{color}|{roughness}|{metal}|{alpha}"
        if key in self._material_index:
            return self._material_index[key]
        entry = {
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": [color[0], color[1], color[2], alpha],
                "metallicFactor": metal,
                "roughnessFactor": roughness,
            },
            "doubleSided": double_sided,
        }
        if alpha < 1.0:
            entry["alphaMode"] = "BLEND"
        self.materials.append(entry)
        self._material_index[key] = len(self.materials) - 1
        return self._material_index[key]

    # -- geometry -----------------------------------------------------------
    def add_box(self, cx: float, cy: float, cz: float, sx: float, sy: float, sz: float,
                vertices: List[float], normals: List[float], indices: List[int]) -> None:
        """Append an axis aligned box centred at (cx,cy,cz) with size (sx,sy,sz)."""
        base = len(vertices) // 3
        hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
        faces = [
            ((-1, 0, 0), [(-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)]),
            ((1, 0, 0), [(hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz)]),
            ((0, -1, 0), [(-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz), (hx, -hy, -hz)]),
            ((0, 1, 0), [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
            ((0, 0, -1), [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)]),
            ((0, 0, 1), [(-hx, hy, -hz), (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz)]),
        ]
        for normal, corners in faces:
            for corner in corners:
                vertices.extend([cx + corner[0], cy + corner[1], cz + corner[2]])
                normals.extend(normal)
            indices.extend([base + 0, base + 1, base + 2, base + 0, base + 2, base + 3])
            base += 4

    def finish_group(self, name: str, material_id: int, vertices: List[float],
                     normals: List[float], indices: List[int]) -> Optional[int]:
        if not indices:
            return None
        bin_offset = len(self.buffer)
        vertex_bytes = struct.pack(f"<{len(vertices)}f", *vertices)
        normal_bytes = struct.pack(f"<{len(normals)}f", *normals)
        index_bytes = struct.pack(f"<{len(indices)}H", *indices)
        # pad each view to 4 bytes
        self.buffer.extend(vertex_bytes)
        self._pad()
        v_off, v_len = bin_offset, len(vertex_bytes)
        n_off = len(self.buffer)
        self.buffer.extend(normal_bytes)
        self._pad()
        n_len = len(normal_bytes)
        i_off = len(self.buffer)
        self.buffer.extend(index_bytes)
        self._pad()
        i_len = len(index_bytes)

        self.buffer_views.append({"buffer": 0, "byteOffset": v_off, "byteLength": v_len,
                                  "target": 34962})
        v_view = len(self.buffer_views) - 1
        self.buffer_views.append({"buffer": 0, "byteOffset": n_off, "byteLength": n_len,
                                  "target": 34962})
        n_view = len(self.buffer_views) - 1
        self.buffer_views.append({"buffer": 0, "byteOffset": i_off, "byteLength": i_len,
                                  "target": 34963})
        i_view = len(self.buffer_views) - 1

        count = len(vertices) // 3
        xs = vertices[0::3]
        ys = vertices[1::3]
        zs = vertices[2::3]
        self.accessors.append({"bufferView": v_view, "componentType": 5126, "count": count,
                               "type": "VEC3",
                               "min": [min(xs), min(ys), min(zs)],
                               "max": [max(xs), max(ys), max(zs)]})
        v_acc = len(self.accessors) - 1
        self.accessors.append({"bufferView": n_view, "componentType": 5126, "count": count,
                               "type": "VEC3"})
        n_acc = len(self.accessors) - 1
        self.accessors.append({"bufferView": i_view, "componentType": 5123,
                               "count": len(indices), "type": "SCALAR"})
        i_acc = len(self.accessors) - 1

        self.meshes.append({
            "name": name,
            "primitives": [{"attributes": {"POSITION": v_acc, "NORMAL": n_acc},
                            "indices": i_acc, "material": material_id, "mode": 4}],
        })
        self.nodes.append({"name": name, "mesh": len(self.meshes) - 1})
        return len(self.nodes) - 1

    def _pad(self) -> None:
        while len(self.buffer) % 4:
            self.buffer.append(0)

    def save(self, name: str) -> bytes:
        gltf = {
            "asset": {"version": "2.0", "generator": "AI Home Designer",
                      "copyright": "Preliminary design model - not a construction document"},
            "scene": 0,
            "scenes": [{"name": name, "nodes": list(range(len(self.nodes)))}],
            "nodes": self.nodes,
            "meshes": self.meshes,
            "materials": self.materials,
            "accessors": self.accessors,
            "bufferViews": self.buffer_views,
            "buffers": [{"byteLength": len(self.buffer)}],
        }
        json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
        json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
        bin_bytes = bytes(self.buffer)
        total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
        out = bytearray()
        out += struct.pack("<III", 0x46546C67, 2, total)
        out += struct.pack("<II", len(json_bytes), 0x4E4F534A)
        out += json_bytes
        out += struct.pack("<II", len(bin_bytes), 0x004E4942)
        out += bin_bytes
        return bytes(out)


def _color_for(group: str, hint: str = "") -> Tuple[float, float, float]:
    palette = {
        "structure": (0.62, 0.62, 0.64),
        "exterior_wall": (0.86, 0.84, 0.80),
        "interior_wall": (0.92, 0.91, 0.88),
        "door": (0.45, 0.30, 0.18),
        "window": (0.30, 0.32, 0.36),
        "glass": (0.55, 0.75, 0.85),
        "stair": (0.55, 0.53, 0.50),
        "roof": (0.55, 0.28, 0.22),
        "floor_finish": (0.72, 0.60, 0.45),
        "furniture": (0.55, 0.45, 0.35),
        "fixture": (0.90, 0.92, 0.94),
        "site": (0.55, 0.57, 0.58),
        "landscape": (0.45, 0.62, 0.35),
    }
    return palette.get(group, (0.7, 0.7, 0.7))


def _split_wall(wall: WallSegment, openings: List[Opening], floor_height: float
                ) -> List[Tuple[Tuple[float, float], Tuple[float, float], str]]:
    """Return solid pieces as ((lo, hi), (z0, z1), kind) where kind is solid/above/below."""
    pieces: List[Tuple[Tuple[float, float], Tuple[float, float], str]] = []
    ordered = sorted([op for op in openings if op.room_id in wall.rooms or True],
                     key=lambda op: op.offset)
    # openings relevant to this wall are passed in already filtered
    spans: List[Tuple[float, float, str, Opening]] = []
    for op in ordered:
        spans.append((op.offset, op.offset + op.width, op.kind, op))
    spans.sort(key=lambda s: s[0])
    cursor = wall.lo
    for lo, hi, kind, op in spans:
        lo = max(lo, wall.lo)
        hi = min(hi, wall.hi)
        if hi <= lo:
            continue
        if lo > cursor:
            pieces.append(((cursor, lo), (0.0, floor_height), "solid"))
        if kind in ("door", "sliding", "pass_through"):
            sill = 0.0
            head = min(op.sill + op.height, floor_height)
        else:
            sill = op.sill
            head = min(op.sill + op.height, floor_height)
        if sill > 0.02:
            pieces.append(((lo, hi), (0.0, sill), "below"))
        if head < floor_height - 0.02:
            pieces.append(((lo, hi), (head, floor_height), "above"))
        cursor = max(cursor, hi)
    if cursor < wall.hi:
        pieces.append(((cursor, wall.hi), (0.0, floor_height), "solid"))
    return pieces


def build_glb(data: ProjectData, include_furniture: bool = True,
              include_site: bool = True) -> bytes:
    builder = _GlbBuilder()
    groups: Dict[str, Tuple[List[float], List[float], List[int]]] = {}

    def group(name: str):
        if name not in groups:
            groups[name] = ([], [], [])
        return groups[name]

    plot = data.plot
    floor_height = 3.0

    for floor in data.floors:
        z0 = floor.index * floor_height
        # slab / floor finish
        vertices, normals, indices = group("structure")
        base = len(vertices) // 3
        _add_box_raw(vertices, normals, indices,
                     plot.width / 2, z0 - SLAB / 2, -plot.depth / 2,
                     plot.width, SLAB, plot.depth)
        for room in floor.rooms:
            if room.is_outdoor:
                continue
            vertices, normals, indices = group("floor_finish")
            _add_box_raw(vertices, normals, indices,
                         room.x + room.w / 2, z0 + 0.015, -(room.y + room.d / 2),
                         room.w, 0.03, room.d)
        # walls split around openings
        walls = derive_walls(floor.rooms)
        for wall in walls:
            openings = [op for op in floor.openings
                        if _on_wall(wall, op, floor)]
            for (lo, hi), (za, zb), _kind in _split_wall(wall, openings, floor_height):
                if wall.axis == "y":
                    cx = (lo + hi) / 2
                    cz = -(wall.line)
                    sx, sz = hi - lo, wall.thickness
                else:
                    cx = wall.line
                    cz = -((lo + hi) / 2)
                    sx, sz = wall.thickness, hi - lo
                vertices, normals, indices = group(
                    "exterior_wall" if wall.kind == "exterior" else "interior_wall")
                _add_box_raw(vertices, normals, indices, cx, z0 + (za + zb) / 2, cz,
                             sx, zb - za, sz)
        # doors and windows
        for op in floor.openings:
            room = floor.room(op.room_id)
            if not room:
                continue
            line, thickness = _opening_line(room, op)
            if line is None:
                continue
            cx, cz = _opening_centre(room, op, line)
            if op.kind in ("door", "sliding", "pass_through"):
                zc = z0 + (op.sill + op.height) / 2
                vertices, normals, indices = group("door")
                _add_opening_box(vertices, normals, indices, room, op, cx, cz, zc,
                                 thickness * 0.35)
            else:
                zc = z0 + op.sill + op.height / 2
                vertices, normals, indices = group("window")
                _add_opening_box(vertices, normals, indices, room, op, cx, cz, zc,
                                 thickness * 0.5, glass=False, frame=True)
                vertices, normals, indices = group("glass")
                _add_opening_box(vertices, normals, indices, room, op, cx, cz, zc,
                                 thickness * 0.18)
        # stairs: two flights and a landing, real treads
        for stair in floor.stairs:
            vertices, normals, indices = group("stair")
            _add_stair(vertices, normals, indices, stair, z0)
        # roof on the top habitable floor
        if floor.index == max(f.index for f in data.floors if any(not r.is_outdoor
                                                                   for r in f.rooms)):
            vertices, normals, indices = group("roof")
            for room in floor.rooms:
                if room.is_outdoor:
                    continue
                _add_box_raw(vertices, normals, indices,
                             room.x + room.w / 2, z0 + floor_height + 0.075,
                             -(room.y + room.d / 2), room.w + 0.3, SLAB, room.d + 0.3)
        # furniture
        if include_furniture:
            for item in floor.furniture:
                group_name = "fixture" if item.kind in (
                    "toilet", "washbasin", "shower", "bathtub", "sink", "hob", "fridge",
                    "washing_machine") else "furniture"
                vertices, normals, indices = group(group_name)
                _add_box_raw(vertices, normals, indices, item.x, z0 + item.z + item.height / 2,
                             -item.y, item.w, item.height, item.d)

    if include_site:
        vertices, normals, indices = group("site")
        _add_box_raw(vertices, normals, indices, plot.width / 2, -0.05, -plot.depth / 2,
                     plot.width + 1.0, 0.1, plot.depth + 1.0)
        vertices, normals, indices = group("landscape")
        for floor in data.floors[:1]:
            for room in floor.rooms:
                if room.is_outdoor and room.type in ("garden", "courtyard", "parking"):
                    _add_box_raw(vertices, normals, indices, room.x + room.w / 2, 0.02,
                                 -(room.y + room.d / 2), room.w, 0.04, room.d)
        if data.materials.boundary_wall:
            vertices, normals, indices = group("site")
            t = 0.15
            h = 1.5
            for (cx, cz, sx, sz) in [
                (plot.width / 2, -0.0, plot.width, t),
                (plot.width / 2, -plot.depth, plot.width, t),
                (0.0, -plot.depth / 2, t, plot.depth),
                (plot.width, -plot.depth / 2, t, plot.depth),
            ]:
                _add_box_raw(vertices, normals, indices, cx, h / 2, cz, sx, h, sz)

    for name in GROUP_ORDER + [g for g in groups if g not in GROUP_ORDER]:
        if name not in groups:
            continue
        vertices, normals, indices = groups[name]
        if not indices:
            continue
        material_id = builder.material(name, _color_for(name),
                                       roughness=0.9 if name != "glass" else 0.15,
                                       metal=0.1 if name == "window" else 0.0,
                                       alpha=0.35 if name == "glass" else 1.0,
                                       double_sided=name in ("glass", "roof"))
        builder.finish_group(name, material_id, vertices, normals, indices)
    return builder.save(data.name or "house")


def _add_stair(vertices, normals, indices, stair, z0: float) -> None:
    half = max(1, stair.risers // 2)
    rest = max(1, stair.risers - half)
    rise, tread = stair.rise_m, stair.tread_m
    along_y = stair.direction in ("N", "S")
    sign = 1 if stair.direction in ("N", "E") else -1
    half_w = stair.width_m / 2.0 if along_y else stair.w / 2.0

    def tread_box(axis_offset: float, z_centre: float, band: str) -> None:
        if along_y:
            cx = stair.x + (stair.w * 0.25 if band == "A" else stair.w * 0.75)
            cz = -(axis_offset)
            sx, sz = half_w, tread
        else:
            cz = -(stair.y + (stair.d * 0.25 if band == "A" else stair.d * 0.75))
            cx = axis_offset
            sx, sz = tread, half_w
        _add_box_raw(vertices, normals, indices, cx, z_centre, cz, sx, rise, sz)

    start = stair.y if (along_y and sign > 0) else (stair.y + stair.d if along_y else
                     (stair.x if sign > 0 else stair.x + stair.w))
    for i in range(half):
        pos = start + sign * tread * (i + 0.5)
        tread_box(pos, z0 + rise * (i + 1) - rise / 2, "A")
    landing_start = start + sign * tread * half
    landing_mid = landing_start + sign * stair.landing_m / 2
    if along_y:
        _add_box_raw(vertices, normals, indices, stair.x + stair.w / 2,
                     z0 + rise * half - rise / 2, -landing_mid, stair.w, rise,
                     stair.landing_m)
    else:
        _add_box_raw(vertices, normals, indices, landing_mid, z0 + rise * half - rise / 2,
                     -(stair.y + stair.d / 2), stair.landing_m, rise, stair.d)
    for j in range(rest):
        pos = landing_start + sign * stair.landing_m - sign * tread * (j + 0.5)
        tread_box(pos, z0 + rise * (half + j + 1) - rise / 2, "B")


def _add_box_raw(vertices, normals, indices, cx, cy, cz, sx, sy, sz) -> None:
    base = len(vertices) // 3
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    faces = [
        ((-1, 0, 0), [(-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)]),
        ((1, 0, 0), [(hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz)]),
        ((0, -1, 0), [(-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz), (hx, -hy, -hz)]),
        ((0, 1, 0), [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
        ((0, 0, -1), [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)]),
        ((0, 0, 1), [(-hx, hy, -hz), (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz)]),
    ]
    for normal, corners in faces:
        for corner in corners:
            vertices.extend([cx + corner[0], cy + corner[1], cz + corner[2]])
            normals.extend(normal)
        indices.extend([base + 0, base + 1, base + 2, base + 0, base + 2, base + 3])
        base += 4


def _on_wall(wall: WallSegment, op: Opening, floor: Floor) -> bool:
    room = floor.room(op.room_id)
    if not room:
        return False
    r = (room.x, room.y, room.w, room.d)
    if op.edge in ("S", "N"):
        line = r[1] if op.edge == "S" else r[1] + r[3]
        if wall.axis != "y" or abs(wall.line - line) > 0.06:
            return False
        return wall.lo - 0.05 <= op.offset <= wall.hi - op.width + 0.05 or (
            op.offset >= wall.lo - 0.05 and op.offset + op.width <= wall.hi + 0.05)
    line = r[0] if op.edge == "W" else r[0] + r[2]
    if wall.axis != "x" or abs(wall.line - line) > 0.06:
        return False
    return op.offset >= wall.lo - 0.05 and op.offset + op.width <= wall.hi + 0.05


def _opening_line(room: Room, op: Opening) -> Tuple[Optional[float], float]:
    r = (room.x, room.y, room.w, room.d)
    if op.edge == "S":
        return r[1], 0.23
    if op.edge == "N":
        return r[1] + r[3], 0.23
    if op.edge == "W":
        return r[0], 0.23
    return r[0] + r[2], 0.23


def _opening_centre(room: Room, op: Opening, line: float) -> Tuple[float, float]:
    mid = op.offset + op.width / 2
    if op.edge in ("S", "N"):
        return mid, -line
    return line, -mid


def _add_opening_box(vertices, normals, indices, room: Room, op: Opening, cx: float, cz: float,
                     zc: float, thickness: float, glass: bool = True,
                     frame: bool = False) -> None:
    if op.edge in ("S", "N"):
        sx, sz = op.width, thickness
    else:
        sx, sz = thickness, op.width
    _add_box_raw(vertices, normals, indices, cx, zc, cz, sx, op.height, sz)
