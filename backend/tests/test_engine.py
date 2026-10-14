"""Compact regression matrix over the deterministic layout engine."""

import itertools

from app.schemas import Requirements
from app.services.designer import make_plot
from app.services.layout.engine import generate_house
from app.services.geometry import derive_walls
from app.services.validation import validate_project


def _overlaps(rooms):
    bad = []
    rects = [(r.x, r.y, r.x + r.w, r.y + r.d, r.id) for r in rooms if not r.is_outdoor]
    for a, b in itertools.combinations(rects, 2):
        ix = min(a[2], b[2]) - max(a[0], b[0])
        iy = min(a[3], b[3]) - max(a[1], b[1])
        if ix > 0.02 and iy > 0.02 and ix * iy > 0.02:
            bad.append((a[4], b[4]))
    return bad


def test_engine_matrix():
    cases = [
        (30, 50, 1, 2, 2), (30, 50, 2, 3, 3), (40, 60, 2, 4, 3), (25, 40, 1, 1, 1),
        (50, 80, 3, 5, 4), (33, 55, 2, 3, 2), (60, 40, 1, 2, 2), (45, 45, 2, 4, 4),
    ]
    for width, depth, floors, bedrooms, bathrooms in cases:
        plot = make_plot(width, depth, "ft")
        req = Requirements(floors=floors, bedrooms=bedrooms, bathrooms=bathrooms,
                           attached_baths=min(1, bathrooms), wants_parking="one_car")
        result = generate_house(plot, req, "family_comfort")
        for floor in result.floors:
            assert not _overlaps(floor.rooms), (width, depth, floors, floor.name)
            for room in floor.rooms:
                assert room.x >= -0.01 and room.y >= -0.01
                assert room.x + room.w <= plot.width + 0.01
                assert room.y + room.d <= plot.depth + 0.01
            assert derive_walls(floor.rooms)


def test_validation_counts_severities():
    plot = make_plot(30, 50, "ft")
    req = Requirements(floors=2, bedrooms=3, bathrooms=3, attached_baths=1)
    result = generate_house(plot, req, "space_efficient")
    from app.schemas import MaterialSet, ProjectData
    data = ProjectData(name="t", plot=plot, requirements=req, floors=result.floors,
                       materials=MaterialSet(), metrics=result.metrics,
                       assumptions=result.assumptions)
    issues = validate_project(data)
    assert all(i.severity in ("error", "warning", "info") for i in issues)
    # engine output must never carry an unexplained blocking overlap
    assert not any(i.code == "overlap" and i.severity == "error" for i in issues)
