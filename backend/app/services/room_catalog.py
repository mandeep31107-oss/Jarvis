"""Room catalogue: dimension rules, semantics and default furniture.

These values drive the layout engine, the validation engine and the 3D furniture
builder. They are planning defaults, not code-mandated minimums -- anything that
is a legal requirement must be confirmed with the local authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class RoomSpec:
    type: str
    label: str
    min_area: float          # m²
    comfort_area: float      # m²
    min_dim: float           # m  (shortest acceptable side)
    max_dim: float           # m
    max_aspect: float = 2.6  # longest / shortest
    habitable: bool = True
    wet: bool = False
    outdoor: bool = False
    needs_window: bool = True
    needs_door: bool = True
    ceiling: float = 3.0
    weight: float = 1.0      # relative area appetite used by the space planner
    furniture: List[str] = field(default_factory=list)
    zone_pref: str = "any"   # front | mid | rear | any  (used by concept strategies)


ROOM_SPECS: Dict[str, RoomSpec] = {
    "living": RoomSpec("living", "Living Room", 11.0, 18.0, 3.0, 7.5, weight=1.35, zone_pref="front",
                       furniture=["sofa_3", "sofa_2", "coffee_table", "tv_unit", "rug", "plant"]),
    "dining": RoomSpec("dining", "Dining Room", 7.0, 12.0, 2.7, 5.5, weight=0.85, zone_pref="mid",
                       furniture=["dining_table", "chairs", "sideboard"]),
    "kitchen": RoomSpec("kitchen", "Kitchen", 5.5, 9.5, 2.2, 4.5, wet=True, weight=0.8, zone_pref="rear",
                        furniture=["kitchen_counter", "kitchen_upper", "sink", "stove", "fridge"]),
    "utility": RoomSpec("utility", "Utility / Wash", 2.2, 4.0, 1.4, 3.0, wet=True, habitable=False,
                        needs_window=True, weight=0.3, zone_pref="rear", furniture=["washing_machine", "utility_shelf"]),
    "bedroom": RoomSpec("bedroom", "Bedroom", 8.5, 12.5, 2.7, 5.5, weight=1.0, zone_pref="rear",
                        furniture=["bed_double", "wardrobe", "side_table", "study_desk_small"]),
    "master_bedroom": RoomSpec("master_bedroom", "Master Bedroom", 11.0, 16.0, 3.0, 6.0, weight=1.2, zone_pref="rear",
                               furniture=["bed_king", "wardrobe_large", "side_table", "dresser", "accent_chair"]),
    "bathroom": RoomSpec("bathroom", "Bathroom", 3.0, 5.0, 1.5, 3.0, wet=True, habitable=False,
                         needs_window=True, weight=0.35, zone_pref="mid",
                         furniture=["toilet", "wash_basin", "shower", "mirror_cabinet"]),
    "toilet": RoomSpec("toilet", "Toilet", 1.6, 2.6, 1.1, 2.4, wet=True, habitable=False,
                       needs_window=True, weight=0.2, zone_pref="mid", furniture=["toilet", "wash_basin"]),
    "study": RoomSpec("study", "Study / Home Office", 6.0, 9.5, 2.4, 4.5, weight=0.6, zone_pref="any",
                      furniture=["desk", "office_chair", "bookshelf"]),
    "puja": RoomSpec("puja", "Prayer Room", 2.5, 4.5, 1.5, 3.0, weight=0.25, zone_pref="front",
                     furniture=["puja_unit", "low_seat"]),
    "store": RoomSpec("store", "Store Room", 2.5, 5.0, 1.4, 3.0, habitable=False, needs_window=False,
                      weight=0.3, zone_pref="any", furniture=["shelving"]),
    "stairs": RoomSpec("stairs", "Staircase", 5.0, 8.0, 2.0, 5.0, habitable=False, needs_window=False,
                       weight=0.45, zone_pref="mid", furniture=[]),
    "lobby": RoomSpec("lobby", "Entrance Lobby", 3.5, 7.0, 1.8, 4.0, needs_window=False, weight=0.4,
                      zone_pref="front", furniture=["shoe_rack", "bench"]),
    "corridor": RoomSpec("corridor", "Corridor", 2.0, 6.0, 1.0, 12.0, max_aspect=10.0, habitable=False,
                         needs_window=False, weight=0.3, zone_pref="any", furniture=[]),
    "balcony": RoomSpec("balcony", "Balcony", 3.0, 6.5, 1.2, 6.0, outdoor=True, habitable=False,
                        needs_window=False, weight=0.3, zone_pref="front", furniture=["balcony_chair", "plant"]),
    "terrace": RoomSpec("terrace", "Terrace", 8.0, 30.0, 2.5, 15.0, outdoor=True, habitable=False,
                        needs_window=False, weight=0.5, zone_pref="any", furniture=["plant", "seating_outdoor"]),
    "garden": RoomSpec("garden", "Garden", 4.0, 20.0, 1.8, 12.0, outdoor=True, habitable=False,
                       needs_window=False, weight=0.4, zone_pref="front", furniture=["plant", "tree"]),
    "courtyard": RoomSpec("courtyard", "Courtyard", 5.0, 16.0, 2.0, 8.0, outdoor=True, habitable=False,
                        needs_window=False, weight=0.45, zone_pref="mid", furniture=["plant", "tulsi_stand"]),
    "parking": RoomSpec("parking", "Parking", 5.0, 15.0, 2.4, 8.0, outdoor=True, habitable=False,
                        needs_window=False, weight=0.6, zone_pref="front", furniture=[]),
    "porch": RoomSpec("porch", "Porch", 3.0, 8.0, 1.5, 6.0, outdoor=True, habitable=False,
                      needs_window=False, weight=0.3, zone_pref="front", furniture=["bench"]),
    "servant_room": RoomSpec("servant_room", "Servant Room", 6.0, 9.0, 2.4, 4.0, weight=0.5, zone_pref="rear",
                             furniture=["bed_single", "wardrobe_small"]),
    "shop": RoomSpec("shop", "Shop / Workspace", 9.0, 20.0, 2.7, 8.0, weight=0.9, zone_pref="front",
                     furniture=["counter_shop", "shelving"]),
    "open_to_below": RoomSpec("open_to_below", "Open to Below", 4.0, 20.0, 2.0, 8.0, habitable=False,
                              needs_window=False, weight=0.2, zone_pref="mid", furniture=[]),
}

ROOM_LABELS = {k: v.label for k, v in ROOM_SPECS.items()}

PARKING_AREA = {
    "none": 0.0,
    "two_wheeler": 4.5,
    "one_car": 12.0,
    "two_car": 22.0,
}

PARKING_DIMS = {
    "none": (0.0, 0.0),
    "two_wheeler": (1.6, 2.8),
    "one_car": (3.0, 5.0),
    "two_car": (5.6, 5.2),
}


def spec(room_type: str) -> RoomSpec:
    return ROOM_SPECS.get(room_type, ROOM_SPECS["bedroom"])


def min_area(room_type: str) -> float:
    return spec(room_type).min_area


def target_area(room_type: str, generosity: float = 1.0) -> float:
    s = spec(room_type)
    return s.comfort_area * generosity


def is_habitable(room_type: str) -> bool:
    return spec(room_type).habitable


def is_wet(room_type: str) -> bool:
    return spec(room_type).wet


def is_outdoor(room_type: str) -> bool:
    return spec(room_type).outdoor
