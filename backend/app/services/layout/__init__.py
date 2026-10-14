"""Deterministic layout + geometry engine (the plan generator)."""

from .concepts import CONCEPTS, Concept, concept_summaries, get_concept
from .engine import GenerationResult, generate_house, stair_geometry
from .furniture import FURNITURE_SPECS, furnish_floor, furnish_room
from .openings import place_openings
from .partition import partition, repair_min_dims, snap_cells
from .site import boundary_points_from_plot, decompose_polygon, offset_polygon

__all__ = ["CONCEPTS", "Concept", "get_concept", "concept_summaries", "GenerationResult",
           "generate_house", "stair_geometry", "FURNITURE_SPECS", "furnish_floor",
           "furnish_room", "place_openings", "partition", "repair_min_dims", "snap_cells",
           "boundary_points_from_plot", "decompose_polygon", "offset_polygon"]
