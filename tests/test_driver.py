import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp import web
from pybyd import BydAuthenticationError, BydControlPasswordError
from pybyd.models.control import RemoteControlResult
from pybyd.models.gps import GpsInfo
from pybyd.models.latest_config import VehicleCapabilities
from pybyd.models.realtime import VehicleRealtimeData
from pybyd.models.vehicle import Vehicle

import byd_vehicle as driver

VIN = "DEVELOPMENTVIN0001"
SETTINGS = driver.Settings("test@example.invalid", "private-password", "IN", "byd:test", "123456")


def fixture_client():
    vehicle = Vehicle(vin=VIN, model_name="Test car")
    caps = VehicleCapabilities(vin=VIN, lock=True, unlock=True, climate=True,
                               location=True, find_car=False, close_windows=None)
    client = SimpleNamespace(
        async_start=AsyncMock(), async_close=AsyncMock(), get_vehicles=AsyncMock(return_value=[vehicle]),
        get_car=AsyncMock(return_value=SimpleNamespace(vin=VIN, capabilities=caps)),
        get_vehicle_realtime=AsyncMock(return_value=VehicleRealtimeData(
            elec_percent=0, endurance_mileage=0, total_mileage=2345, temp_in_car=22)),
        get_hvac_status=AsyncMock(side_effect=RuntimeError("Unavailable")),
        get_gps_info=AsyncMock(return_value=GpsInfo(latitude=0, longitude=0)),
        verify_command_access=AsyncMock(),
        lock=AsyncMock(return_value=RemoteControlResult(controlState=1, success=True)),
        unlock=AsyncMock(return_value=RemoteControlResult(controlState=1, success=True)),
        start_climate=AsyncMock(return_value=RemoteControlResult(controlState=1, success=True)),
        stop_climate=AsyncMock(return_value=RemoteControlResult(controlState=1, success=True)),
    )
    return client


@pytest.fixture
async def runtime():
    events = []
    client = fixture_client()

    async def emit(event):
        events.append(event)

    instance = driver.Runtime(SETTINGS, emit, lambda config: client)
    await instance.start()
    yield instance, client, events
    await instance.close()


def action(identifier, key, payload=None, request="request-1", event="COMMAND_EXECUTE"):
    return {"event": event, "device_id": identifier, "data": {
        "command": key, "payload": payload or {}, "requestId": request, "correlationId": request}}


def last_result(events):
    return [e["data"] for e in events if e["event"] == "ACTION_RESULT"][-1]


@pytest.mark.parametrize("country,region", [("IN", "in"), ("GB", "eu"), ("AU", "au"),
                                         ("NZ", "au"), ("BR", "br"), ("SA", "sa"), ("SG", "sg")])
def test_country_routes(country, region):
    assert replace(SETTINGS, country=country).client_config().base_url == f"https://dilinkappoversea-{region}.byd.auto"


def test_configuration_requires_country_and_hides_secrets(monkeypatch):
    monkeypatch.setenv("BYD_USERNAME", "private@example.invalid")
    monkeypatch.setenv("BYD_PASSWORD", "private-password")
    monkeypatch.setenv("BYD_COUNTRY_CODE", "CN")
    with pytest.raises(ValueError, match="country") as caught:
        driver.Settings.from_env()
    assert "private" not in str(caught.value)


@pytest.mark.parametrize("key,value,match", [("BYD_POLL_SECONDS", "1", "interval"),
                                          ("BYD_POLL_SECONDS", "wrong", "interval"),
                                          ("BYD_CONTROL_PIN", "bad", "six digits"),
                                          ("BYD_INCLUDE_LOCATION", "bad", "true or false"),
                                          ("HOS_WS_URL", "ws://remote.example/driver", "secure"),
                                          ("HOS_WS_URL", "wss://user:secret@example.com/driver", "address")])
def test_invalid_settings(monkeypatch, key, value, match):
    monkeypatch.setenv("BYD_USERNAME", SETTINGS.username)
    monkeypatch.setenv("BYD_PASSWORD", SETTINGS.password)
    monkeypatch.setenv("BYD_COUNTRY_CODE", "IN")
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=match):
        driver.Settings.from_env()


def test_device_ids_are_stable_and_account_scoped():
    assert driver.device_id("one", VIN) == driver.device_id("one", VIN)
    assert driver.device_id("one", VIN) != driver.device_id("two", VIN)
    assert VIN not in driver.device_id("one", VIN)


async def test_discovery_and_zero_telemetry_without_location(runtime):
    instance, client, events = runtime
    identifier = next(iter(instance.cars))
    discovery = events[0]
    assert discovery["event"] == "DEVICE_DISCOVERED"
    assert discovery["data"]["driverInstanceId"] == "byd:test"
    keys = {row["key"] for row in discovery["data"]["properties"]["commandCatalog"]}
    assert keys == {"refresh_state", "lock", "unlock", "start_climate", "stop_climate"}
    assert await instance.refresh(identifier)
    extras = next(e["data"]["extras"] for e in events if e["event"] == "STATE_UPDATE")
    assert extras["sensors"]["battery"]["value"] == 0
    assert extras["sensors"]["locked"]["value"] is None
    assert extras["sensors"]["online"]["value"] is None
    assert extras["sensors"]["climate"]["value"] is None
    assert extras["location"] is None
    client.get_gps_info.assert_not_called()
    assert all(secret not in json.dumps(events) for secret in [SETTINGS.password, SETTINGS.username, VIN, "123456"])


async def test_location_is_opt_in_and_zero_coordinates_are_valid(runtime):
    instance, client, events = runtime
    instance.settings = replace(instance.settings, include_location=True)
    assert await instance.refresh(next(iter(instance.cars)))
    extras = next(e["data"]["extras"] for e in events if e["event"] == "STATE_UPDATE")
    assert extras["location"] == {"latitude": 0, "longitude": 0}
    client.get_gps_info.assert_awaited_once()


async def test_confirmed_command_correlates_and_uses_exact_vin(runtime):
    instance, client, events = runtime
    identifier = next(iter(instance.cars))
    await instance.handle(action(identifier, "lock"))
    assert last_result(events)["success"] is True
    assert last_result(events)["requestId"] == "request-1"
    assert last_result(events)["correlationId"] == "request-1"
    client.verify_command_access.assert_awaited_once_with(VIN)
    client.lock.assert_awaited_once_with(VIN)
    assert last_result(events)["stateRefresh"] == "updated"
    await instance.handle(action(identifier, "lock"))
    client.lock.assert_awaited_once()


async def test_identifier_reuse_cannot_change_command(runtime):
    instance, client, events = runtime
    identifier = next(iter(instance.cars))
    await instance.handle(action(identifier, "lock"))
    await instance.handle(action(identifier, "unlock"))
    assert last_result(events)["success"] is False
    client.unlock.assert_not_called()


@pytest.mark.parametrize("state,success", [(0, True), (0, False), (2, False)])
async def test_acceptance_and_pending_results_are_not_success(runtime, state, success):
    instance, client, events = runtime
    client.lock.return_value = RemoteControlResult(controlState=state, success=success)
    await instance.handle(action(next(iter(instance.cars)), "lock"))
    assert last_result(events)["success"] is False


async def test_status_only_has_no_remote_controls(runtime):
    instance, client, events = runtime
    instance.settings = replace(instance.settings, control_pin=None)
    assert driver.command_catalog(next(iter(instance.cars.values())).capabilities, False) == [
        {"key": "refresh_state", "label": "Refresh", "idempotent": True}]
    await instance.handle(action(next(iter(instance.cars)), "unlock"))
    assert last_result(events)["success"] is False
    client.verify_command_access.assert_not_called()
    client.unlock.assert_not_called()


@pytest.mark.parametrize("payload", [{"temperature": 100}, {"duration": 7}, {"duration": 10.5},
                                     {"temperature": True}, {"duration": True}, {"arbitrary": "raw"}])
async def test_climate_rejects_invalid_payload_before_any_write(runtime, payload):
    instance, client, events = runtime
    await instance.handle(action(next(iter(instance.cars)), "start_climate", payload))
    assert last_result(events)["success"] is False
    client.start_climate.assert_not_called()
    client.verify_command_access.assert_not_called()


async def test_valid_climate_uses_bybyd_units(runtime):
    instance, client, events = runtime
    await instance.handle(action(next(iter(instance.cars)), "start_climate", {"temperature": 21, "duration": 20}))
    params = client.start_climate.await_args.kwargs["params"]
    assert params.temperature == 21
    assert params.time_span == 3
    assert last_result(events)["success"] is True


async def test_wrong_car_cannot_execute(runtime):
    instance, client, events = runtime
    await instance.handle(action("unknown", "lock"))
    assert last_result(events)["success"] is False
    client.lock.assert_not_called()


async def test_poll_failure_retains_stale_status_without_secret_error(runtime):
    instance, client, events = runtime
    client.get_vehicle_realtime.side_effect = BydAuthenticationError(SETTINGS.password)
    assert not await instance.refresh(next(iter(instance.cars)))
    assert events[-1]["data"]["extras"]["stale"] is True
    assert SETTINGS.password not in json.dumps(events)


async def test_pin_failure_never_dispatches_and_is_redacted(runtime):
    instance, client, events = runtime
    client.verify_command_access.side_effect = BydControlPasswordError("123456 secret")
    await instance.handle(action(next(iter(instance.cars)), "lock"))
    assert last_result(events)["success"] is False
    assert "123456" not in json.dumps(events)
    client.lock.assert_not_called()


async def test_manual_refresh_is_throttled(runtime):
    instance, client, events = runtime
    identifier = next(iter(instance.cars))
    await instance.refresh(identifier)
    await instance.handle(action(identifier, "refresh_state"))
    assert last_result(events)["success"] is False
    client.get_vehicle_realtime.assert_awaited_once()


async def test_failure_after_confirmed_write_does_not_fake_command_failure(runtime):
    instance, client, events = runtime
    client.get_vehicle_realtime.side_effect = OSError("secret")
    await instance.handle(action(next(iter(instance.cars)), "lock"))
    assert last_result(events)["success"] is True
    assert last_result(events)["stateRefresh"] == "pending"


async def test_cancelled_write_is_not_retried_with_same_request(runtime):
    instance, client, events = runtime
    reached = asyncio.Event()

    async def pending(vin):
        reached.set()
        await asyncio.Event().wait()

    client.lock.side_effect = pending
    message = action(next(iter(instance.cars)), "lock")
    task = asyncio.create_task(instance.handle(message))
    await reached.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await instance.handle(message)
    assert last_result(events)["success"] is False
    client.lock.assert_awaited_once()


async def test_real_websocket_registration_discovery_command_and_shutdown(monkeypatch):
    client = fixture_client()
    monkeypatch.setattr(driver, "BydClient", lambda config: client)
    events = []
    got_result = asyncio.Event()
    stop = asyncio.Event()

    async def handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"event": "CONNECTED", "ok": True})
        registration = await ws.receive_json()
        assert registration["method"] == "driver.register"
        assert registration["protocolVersion"] == 1
        assert registration["params"]["instanceId"] == "byd:test"
        await ws.send_json({"event": "REGISTERED", "ok": True})
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            event = json.loads(message.data)
            events.append(event)
            if event["event"] == "DEVICE_DISCOVERED":
                await ws.send_json(action(event["device_id"], "lock"))
            if event["event"] == "ACTION_RESULT":
                got_result.set()
        return ws

    app = web.Application()
    app.router.add_get("/driver", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    settings = replace(SETTINGS, controller_url=f"ws://127.0.0.1:{port}/driver")
    task = asyncio.create_task(driver.websocket(settings, stop))
    try:
        await asyncio.wait_for(got_result.wait(), 8)
        assert last_result(events)["success"] is True
        assert {e["event"] for e in events} >= {"DEVICE_DISCOVERED", "STATE_UPDATE", "METRIC_UPDATE", "ACTION_RESULT"}
    finally:
        stop.set()
        await asyncio.wait_for(task, 8)
        await runner.cleanup()
    client.async_close.assert_awaited_once()
