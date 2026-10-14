def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] and body["auth_enabled"] is False


def test_project_lifecycle(client, project):
    pid = project["id"]
    assert project["data"]["floors"], "engine produced no floors"
    listed = client.get("/api/projects").json()["projects"]
    assert any(p["id"] == pid for p in listed)
    saved = client.put(f"/api/projects/{pid}", json={"data": project["data"],
                                                     "label": "pytest save"})
    assert saved.status_code == 200
    versions = client.get(f"/api/projects/{pid}/versions").json()["versions"]
    assert versions, "no version recorded"
    renamed = client.patch(f"/api/projects/{pid}/rename", json={"name": "Renamed House"})
    assert renamed.status_code == 200


def test_command_preview_commit_flow(client, project):
    pid = project["id"]
    response = client.post(f"/api/projects/{pid}/command",
                           json={"text": "rename the kitchen to cooking room"})
    assert response.status_code == 200
    payload = response.json()
    if payload["status"] == "preview":
        commit = client.post(f"/api/projects/{pid}/command/commit",
                             json={"preview_id": payload["preview_id"]})
        assert commit.status_code == 200
        names = [r["name"] for f in commit.json()["data"]["floors"] for r in f["rooms"]]
        assert "Cooking Room" in names


def test_sharing_is_off_by_default_and_revocable(client, project):
    pid = project["id"]
    off = client.get(f"/api/projects/{pid}").json()
    assert off["sharing_enabled"] is False
    on = client.post(f"/api/projects/{pid}/sharing", json={"enabled": True}).json()
    token = on["share_token"]
    assert token and client.get(f"/api/shared/{token}").status_code == 200
    client.post(f"/api/projects/{pid}/sharing", json={"enabled": False})
    assert client.get(f"/api/shared/{token}").status_code == 404


def test_upload_validation_rejects_junk(client, project):
    pid = project["id"]
    response = client.post(f"/api/projects/{pid}/images",
                           files={"files": ("x.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert response.status_code == 400


def test_settings_never_leak_keys(client):
    response = client.put("/api/settings", json={"values": {"openai_api_key": "sk-test-123456"}})
    assert response.status_code == 200
    providers = client.get("/api/settings/providers").json()
    masked = providers["runtime"].get("openai_api_key", "")
    assert "sk-test-123456" not in str(providers)
    assert masked != "sk-test-123456"
    client.put("/api/settings", json={"values": {"openai_api_key": ""}})
