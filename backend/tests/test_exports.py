import json
import struct


def test_plan_svg_and_png(project, client):
    pid = project["id"]
    floor_id = project["data"]["floors"][0]["id"]
    response = client.post(f"/api/projects/{pid}/exports",
                           json={"kind": "plan_svg", "floor_id": floor_id,
                                 "all_floors": False, "inline": True})
    assert response.status_code == 200
    assert response.content.startswith(b"<svg")
    response = client.post(f"/api/projects/{pid}/exports",
                           json={"kind": "plan_png", "floor_id": floor_id,
                                 "all_floors": False, "inline": True})
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_report_pdf_and_glb(project, client):
    pid = project["id"]
    response = client.post(f"/api/projects/{pid}/exports", json={"kind": "report_pdf"})
    assert response.status_code == 200
    download = client.get(response.json()["url"])
    assert download.content[:5] == b"%PDF-"
    response = client.post(f"/api/projects/{pid}/exports", json={"kind": "glb"})
    download = client.get(response.json()["url"])
    magic, version, total = struct.unpack("<III", download.content[:12])
    assert magic == 0x46546C67 and version == 2 and total == len(download.content)
    length = struct.unpack("<I", download.content[12:16])[0]
    doc = json.loads(download.content[20:20 + length])
    assert doc["meshes"] and doc["materials"] and doc["accessors"]


def test_json_document_carries_disclaimer(project, client):
    pid = project["id"]
    response = client.post(f"/api/projects/{pid}/exports", json={"kind": "json"})
    download = client.get(response.json()["url"])
    payload = json.loads(download.content)
    assert payload["export"]["not_a_construction_document"] is True
    assert payload["project"]["floors"]
