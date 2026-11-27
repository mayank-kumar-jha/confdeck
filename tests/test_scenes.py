import asyncio

from confdeck.models import Camera


async def test_scene_parallel_partial_failure_and_roundtrip(runtime, client, monkeypatch):
    cameras = [Camera(ip=f"127.0.0.{n}", control_enabled=True, control_protocol="visca", preset_records={"21": "Room"}) for n in (2, 3)]
    runtime.settings.cameras.extend(cameras)
    scene = dict(id="test", name="Room", presets={c.id: "21" for c in cameras})
    assert (await client.put("/api/scenes", json=[scene])).status_code == 200
    assert (await client.get("/api/scenes")).json() == [scene]
    started = set()
    async def recall(cid, token):
        started.add(cid)
        await asyncio.sleep(.02)
        assert len(started) == 2
        if cid == cameras[1].id:
            raise ValueError("Camera offline")
        return {"ok": True}
    monkeypatch.setattr(runtime.follow, "recall", recall)
    result = await client.post("/api/scenes/test/recall", json={})
    assert result.json()[cameras[0].id]["ok"]
    assert result.json()[cameras[1].id] == {"ok": False, "error": "Camera offline"}


async def test_capture_saves_before_linking_and_updates_one_scene(runtime, client, monkeypatch):
    from unittest.mock import AsyncMock

    from confdeck.models import Scene
    save = AsyncMock()
    monkeypatch.setattr("confdeck.visca.ViscaIpDriver.save", save)
    camera = Camera(ip="127.0.0.2", control_enabled=True, control_protocol="visca")
    runtime.settings.cameras.append(camera)
    payload = {"camera": camera.id, "name": "Neha"}
    result = await client.post("/api/scenes/capture", json=payload)
    assert result.status_code == 200, result.text
    save.assert_awaited_once_with("21")
    assert runtime.settings.scenes[0].presets == {camera.id: "21"}
    assert runtime.settings.cameras[0].preset_records == {"21": "Neha"}
    assert (await client.post("/api/scenes/capture", json=payload)).status_code == 400
    runtime.settings.scenes.append(Scene(id="duplicate", name="neha", presets={camera.id: "2"}))
    result = await client.post("/api/scenes/capture", json={**payload, "confirm_update": True})
    assert result.status_code == 200, result.text
    assert len(runtime.settings.scenes) == 1
    assert runtime.settings.scenes[0].presets[camera.id] == "22"
    result = await client.post("/api/scenes/capture", json={**payload, "confirm_update": True})
    assert result.status_code == 200
    assert runtime.settings.scenes[0].presets[camera.id] == "22"


async def test_failed_capture_does_not_create_scene(runtime, client, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("confdeck.visca.ViscaIpDriver.save", AsyncMock(side_effect=ValueError("No acknowledgement")))
    camera = Camera(ip="127.0.0.2", control_enabled=True, control_protocol="visca")
    runtime.settings.cameras.append(camera)
    response = await client.post("/api/scenes/capture", json={"camera": camera.id, "name": "Test"})
    assert response.status_code == 400
    assert runtime.settings.scenes == []
    assert camera.preset_records == {}


async def test_live_seat_edit_is_rejected(runtime, client):
    runtime.follow.live = True
    settings = runtime.settings.tracking.model_dump()
    settings["zones"] = [{"id": "seat", "name": "Seat", "azimuth": 30, "elevation": 45, "radius": 25}]
    response = await client.put("/api/tracking/settings", json=settings)
    assert response.status_code == 400
    assert runtime.settings.tracking.zones == []


async def test_remove_stale_scene_without_validating_others(runtime, client):
    from confdeck.models import Scene
    from confdeck.tracking import Zone
    runtime.settings.scenes = [Scene(id="old", name="Old", presets={"missing":"21"}), Scene(id="keep", name="Keep", presets={"also-missing":"22"})]
    runtime.settings.tracking.zones = [Zone(id="seat", name="Seat", azimuth=90, elevation=45, scene_id="old", presets={"missing":"21"})]
    assert (await client.delete('/api/scenes/old')).status_code == 400
    assert len(runtime.settings.scenes) == 2
    result = await client.delete('/api/scenes/old?confirm_unlink=true')
    assert result.status_code == 200, result.text
    assert result.json()['unlinked_seats'] == ['Seat']
    assert [s.id for s in runtime.settings.scenes] == ['keep']
    zone = runtime.settings.tracking.zones[0]
    assert zone.scene_id == '' and zone.presets == {}
    assert (zone.azimuth, zone.elevation) == (90, 45)

async def test_recall_valid_mapping_despite_removed_camera(runtime, client, monkeypatch):
    from confdeck.models import Scene
    from unittest.mock import AsyncMock
    camera = Camera(ip="127.0.0.2", control_enabled=True, control_protocol="visca", preset_records={"21":"Saved"})
    runtime.settings.cameras.append(camera)
    runtime.settings.scenes = [Scene(id="mixed", name="Mixed", presets={"removed":"21",camera.id:"21"})]
    recall = AsyncMock(return_value={"ok":True})
    monkeypatch.setattr(runtime.follow, "recall", recall)
    result = (await client.post('/api/scenes/mixed/recall',json={})).json()
    assert result[camera.id]['ok'] and not result['removed']['ok']
    recall.assert_awaited_once_with(camera.id, '21')


async def test_native_tracking_readback_and_control_interlock(runtime, client, monkeypatch):
    import httpx
    from unittest.mock import AsyncMock, MagicMock
    camera = Camera(ip="127.0.0.2", control_enabled=True, control_protocol="visca")
    runtime.settings.cameras.append(camera)
    response = MagicMock()
    response.json.side_effect = [
        {"nRetVal":0,"stValue":{"bEnable":0,"nTrackMode":1}},
        {"nRetVal":0},
        {"nRetVal":0,"stValue":{"bEnable":1,"nTrackMode":0}}]
    transport = AsyncMock()
    transport.get.return_value = response
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=transport)
    context.__aexit__ = AsyncMock()
    monkeypatch.setattr(httpx, 'AsyncClient', MagicMock(return_value=context))
    result = await client.post(f'/api/cameras/{camera.id}/native-tracking',json={"enabled":True,"mode":"presenter"})
    assert result.status_code == 200, result.text
    assert camera.native_tracking and not camera.control_enabled
    assert not runtime.follow.live
    result = await client.patch(f'/api/cameras/{camera.id}',json={"control_enabled":True})
    assert result.status_code == 400

    response.json.side_effect = [
        {"nRetVal":0,"stValue":{"bEnable":1,"nTrackMode":0}},
        {"nRetVal":0},
        {"nRetVal":0,"stValue":{"bEnable":0,"nTrackMode":0}}]
    monkeypatch.setattr('confdeck.visca.ViscaIpDriver.check', AsyncMock(return_value={"ok":True}))
    camera.control_protocol = "none"
    result = await client.post(f'/api/cameras/{camera.id}/native-tracking',json={"enabled":False,"mode":"presenter"})
    assert result.status_code == 200, result.text
    assert not camera.native_tracking and camera.control_enabled
    assert camera.control_protocol == "visca"

async def test_camera_add_retains_control_configuration(runtime, client, monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr(runtime.media, 'probe', AsyncMock())
    result = await client.post('/api/cameras',json={"ip":"127.0.0.3","path":"/stream2","control_enabled":True,"control_protocol":"visca","visca_port":1259})
    assert result.status_code == 200, result.text
    camera = next(c for c in runtime.settings.cameras if c.id == result.json()['id'])
    assert camera.control_enabled and camera.control_protocol == 'visca'
    assert camera.visca_port == 1259

async def test_canon_capture_and_existing_preset_assignment(runtime, client, monkeypatch):
    from unittest.mock import AsyncMock
    from confdeck.tracking import Zone

    camera = Camera(ip="127.0.0.2", control_enabled=True, control_protocol="canon")
    runtime.settings.cameras.append(camera)
    catalog = {"limit": 100, "presets": [{"token": "21", "name": "Starting view"}]}
    monkeypatch.setattr("confdeck.ptz.CameraControl.list", AsyncMock(return_value=catalog))
    execute = AsyncMock(return_value={"ok": True, "token": "22"})
    monkeypatch.setattr("confdeck.ptz.CameraControl.execute", execute)
    payload = {"camera": camera.id, "name": "Lectern"}
    captured = await client.post("/api/scenes/capture", json=payload)
    assert captured.json()["scene"]["presets"] == {camera.id: "22"}
    execute.assert_awaited_once_with("save", "22", "Lectern")
    linked = await client.post("/api/scenes/capture", json={
        **payload, "name": "Starting view", "existing_token": "21",
    })
    assert linked.json()["scene"]["presets"] == {camera.id: "21"}
    assert execute.await_count == 1
    settings = runtime.settings.tracking.model_copy(deep=True)
    settings.zones = [Zone(id="seat", name="Lectern", azimuth=10, elevation=45,
                           scene_id=linked.json()["scene"]["id"])]
    response = await client.put("/api/tracking/settings", json=settings.model_dump())
    assert response.status_code == 200, response.text
    assert runtime.settings.tracking.zones[0].presets == {camera.id: "21"}
