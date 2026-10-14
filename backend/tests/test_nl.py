import asyncio

from app.services.designer import quick_project
from app.services.nl import commands


def _run(text, data):
    parsed = asyncio.get_event_loop().run_until_complete(commands.interpret(data, text))
    return parsed


def test_resize_preview_and_commit():
    data = quick_project(30, 50, "ft", floors=2, bedrooms=3, bathrooms=3, attached_baths=1)
    before = {r.id: r.area for r in data.all_rooms()}
    applied = None
    for text in ("make the utility bigger", "make the common bathroom smaller",
                 "make the living room bigger"):
        parsed = _run(text, data)
        if not parsed.operations:
            continue
        preview = commands.preview(data, parsed)
        if preview["applied"] and not preview["rejected"]:
            applied = (text, preview)
            break
    assert applied, "every resize command was refused on a normal plan"
    preview = applied[1]
    assert preview["preview"] is not data
    merged = commands.apply_preview(preview["preview"], data, applied[0])
    after = {r.id: r.area for r in merged.all_rooms()}
    assert after != before


def test_unknown_command_asks_instead_of_guessing():
    data = quick_project(30, 50, "ft", floors=1, bedrooms=2, bathrooms=2)
    parsed = _run("blorp the wuzzle", data)
    assert parsed.clarification
    assert not parsed.operations


def test_missing_room_is_honest():
    data = quick_project(30, 50, "ft", floors=1, bedrooms=2, bathrooms=2)
    parsed = _run("make the aquarium bigger", data)
    assert parsed.clarification or not parsed.operations
