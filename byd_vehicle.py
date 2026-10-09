#!/usr/bin/env python3
"""Standalone Haptique OS driver. stdout is JSON; credentials never enter events."""
# MIT License
# Copyright (c) 2026 Cantata Communication Solutions and contributors
# Country-to-region mapping adapted from hass-byd-vehicle:
# Copyright (c) 2026 jkaberg
# https://github.com/jkaberg/hass-byd-vehicle
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import signal
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

if sys.version_info < (3, 11):
    sys.exit("Python 3.11 or newer is required.")

import aiohttp
from pydantic import ValidationError
from pybyd import BydClient, BydConfig
from pybyd.models import minutes_to_time_span
from pybyd.models.control import ClimateStartParams, ControlState

DRIVER_KEY = "BYD_VEHICLE"
VERSION = "0.1.0-beta.1"
# Region facts from hass-byd-vehicle/const.py; attribution in THIRD_PARTY_NOTICES.md.
REGION_COUNTRIES = {
    "eu": "NO NL DE DK SE FR AT LU BE FI IT ES PT GB IE IS IL HU MT GR CH PL CY EE LV LT CZ RO SK SI BG HR LI ME RS BA MK AL MD MC VA XK UA",
    "sg": "SG TH MY HK MO KH LA PH BN MM NP BD PK LK PF NC MN BT MV",
    "au": "AU NZ", "br": "BR", "jp": "JP", "uz": "UZ",
    "no": "AE KW QA MA BH JO ZA RE MU EG",
    "mx": "MX CL UY CO DO CR PE EC PY BO PA GT SV HN NI AR",
    "id": "ID", "tr": "TR", "kr": "KR", "in": "IN", "vn": "VN",
    "sa": "SA", "om": "OM", "kz": "KZ",
}
COUNTRY_REGION = {country: region for region, countries in REGION_COUNTRIES.items()
                  for country in countries.split()}
REMOTE = {
    "lock": ("lock", "Lock"), "unlock": ("unlock", "Unlock"),
    "start_climate": ("climate", "Start climate"),
    "stop_climate": ("climate", "Stop climate"),
    "find_car": ("find_car", "Find car"),
    "flash_lights": ("flash_lights", "Flash lights"),
    "close_windows": ("close_windows", "Close windows"),
}


def error_message(error: Exception) -> str:
    """Do not echo upstream messages: they can contain PINs, VINs or tokens."""
    name = type(error).__name__
    if name in {"BydAuthenticationError", "BydSessionExpiredError"}:
        return "Sign-in failed. Check account and country."
    if name == "BydControlPasswordError":
        return "Control unavailable. Check the control PIN in the BYD app."
    if name == "BydRateLimitError":
        return "Too many requests. Wait before retrying."
    if name in {"TimeoutError", "BydRemoteControlError"}:
        return "Command not confirmed. Check the car before retrying."
    if name == "BydEndpointNotSupportedError":
        return "Feature unavailable for this car."
    return "Can't connect. Try again later."


def boolean(value: str) -> bool:
    if value.lower() in {"1", "true", "yes", "on"}:
        return True
    if value.lower() in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("Use true or false for location sharing.")


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    country: str
    instance_id: str
    control_pin: str | None = None
    poll_seconds: int = 900
    include_location: bool = False
    language: str = "en"
    time_zone: str = "UTC"
    controller_url: str = "ws://127.0.0.1:8080/driver"

    @classmethod
    def from_env(cls) -> Settings:
        country = os.getenv("BYD_COUNTRY_CODE", "").strip().upper()
        username = os.getenv("BYD_USERNAME", "").strip()
        password = os.getenv("BYD_PASSWORD", "")
        if not username or not password:
            raise ValueError("Enter your BYD account and password.")
        if country not in COUNTRY_REGION:
            raise ValueError("Enter a supported two-letter account country.")
        try:
            poll = int(os.getenv("BYD_POLL_SECONDS", "900"))
        except ValueError:
            raise ValueError("Refresh interval must be a whole number.") from None
        if not 300 <= poll <= 28800:
            raise ValueError("Refresh interval must be between 300 and 28800 seconds.")
        instance = os.getenv("HAPTIQUE_DRIVER_ID", "").strip() or (
            "byd:" + hashlib.sha256(username.encode()).hexdigest()[:16])
        if not 1 <= len(instance) <= 128 or any(
                not (char.isascii() and (char.isalnum() or char in ":_-")) for char in instance):
            raise ValueError("Invalid controller instance identifier.")
        port = os.getenv("PORT", "8080")
        controller = os.getenv("HOS_WS_URL", "").strip() or f"ws://127.0.0.1:{port}/driver"
        url = urlparse(controller)
        if (url.scheme not in {"ws", "wss"} or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or url.path.rstrip("/") != "/driver"):
            raise ValueError("Invalid controller address.")
        if url.scheme == "ws" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use a secure connection for a remote controller.")
        pin = os.getenv("BYD_CONTROL_PIN", "").strip() or None
        if pin is not None and (not pin.isascii() or not pin.isdigit() or len(pin) != 6):
            raise ValueError("Control PIN must contain six digits.")
        return cls(username, password, country, instance, pin, poll,
                   boolean(os.getenv("BYD_INCLUDE_LOCATION", "false")),
                   os.getenv("BYD_LANGUAGE", "en"), os.getenv("BYD_TIME_ZONE", "UTC"), controller)

    def client_config(self) -> BydConfig:
        region = COUNTRY_REGION[self.country]
        try:
            return BydConfig(username=self.username, password=self.password,
                             country_code=self.country, language=self.language,
                             time_zone=self.time_zone, control_pin=self.control_pin,
                             base_url=f"https://dilinkappoversea-{region}.byd.auto")
        except ValidationError:
            raise ValueError("Invalid account settings.") from None


def device_id(instance: str, vin: str) -> str:
    digest = hashlib.sha256(f"{instance}|{vin}".encode()).hexdigest()[:24]
    return f"byd:{digest}"


def command_catalog(capabilities: Any, pin: bool) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = [{"key": "refresh_state", "label": "Refresh", "idempotent": True}]
    for key, (capability, label) in REMOTE.items():
        if not pin or getattr(capabilities, capability, None) is not True:
            continue
        row: dict[str, Any] = {"key": key, "label": label,
                               "idempotent": key not in {"find_car", "flash_lights"}}
        if key == "start_climate":
            row["payloadSchema"] = {"type": "object", "additionalProperties": False, "properties": {
                "temperature": {"type": "number", "minimum": 15, "maximum": 31},
                "duration": {"type": "integer", "enum": [10, 15, 20, 25, 30]},
            }}
        commands.append(row)
    return commands


def state_payload(realtime: Any, hvac: Any = None, gps: Any = None) -> dict[str, Any]:
    """Use measured data only; preserve unknown and genuine zero readings."""
    fields = [
        ("battery", "Battery", "elec_percent", "%"),
        ("range", "Range", "endurance_mileage", "km"),
        ("odometer", "Odometer", "total_mileage", "km"),
        ("temperature", "Cabin", "temp_in_car", "°C"),
    ]
    sensors: dict[str, Any] = {}
    for key, label, field, unit in fields:
        value = getattr(realtime, field, None)
        if isinstance(value, float) and not math.isfinite(value):
            value = None
        sensors[key] = {"name": label, "value": value, "unit": unit}
    sensors["locked"] = {"name": "Locked", "value": realtime.is_locked}
    charging = realtime.effective_charging_state
    sensors["charging"] = {"name": "Charging", "value":
                           charging.name.lower() if int(charging) >= 0 else None}
    sensors["online"] = {"name": "Online", "value":
                         realtime.is_online if int(realtime.online_state) >= 0 else None}
    if hvac is not None:
        sensors["climate"] = {"name": "Climate", "value":
                              hvac.status.name.lower() if int(hvac.status) >= 0 else None}
    else:
        sensors["climate"] = {"name": "Climate", "value": None}
    location = None
    if gps is not None and gps.latitude is not None and gps.longitude is not None:
        lat, lon = gps.latitude, gps.longitude
        if math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            location = {"latitude": lat, "longitude": lon}
    return {"extras": {"sensors": sensors, "location": location, "status": "connected",
                       "stale": False, "lastUpdated": datetime.now(timezone.utc).isoformat()}}


Sink = Callable[[dict[str, Any]], Awaitable[None]]


class Runtime:
    def __init__(self, settings: Settings, sink: Sink, client_factory: Any = None) -> None:
        self.settings = settings
        self.sink = sink
        self.client_factory = client_factory or BydClient
        self.client: Any = None
        self.cars: dict[str, Any] = {}
        self.vehicles: dict[str, Any] = {}
        self.io_lock = asyncio.Lock()
        self.results: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
        self.next_refresh: dict[str, float] = {}

    async def emit(self, event: str, identifier: str, data: dict[str, Any]) -> None:
        await self.sink({"event": event, "driver": DRIVER_KEY.lower(),
                         "device_id": identifier, "data": data})

    async def start(self) -> None:
        self.client = self.client_factory(self.settings.client_config())
        await self.client.async_start()
        await self.discover()

    async def close(self) -> None:
        if self.client is not None:
            await self.client.async_close()
            self.client = None

    async def discover(self) -> None:
        vehicles = await asyncio.wait_for(self.client.get_vehicles(), 60)
        if not vehicles:
            raise ValueError("No vehicles on this account.")
        cars, metadata = {}, {}
        for vehicle in vehicles:
            if not vehicle.vin:
                continue
            identifier = device_id(self.settings.instance_id, vehicle.vin)
            car = await asyncio.wait_for(self.client.get_car(vehicle.vin, vehicle=vehicle), 60)
            cars[identifier], metadata[identifier] = car, vehicle
        if not cars:
            raise ValueError("No vehicles on this account.")
        self.cars, self.vehicles = cars, metadata
        await self.announce()

    async def announce(self) -> None:
        for identifier, car in self.cars.items():
            vehicle = self.vehicles[identifier]
            await self.emit("DEVICE_DISCOVERED", identifier, {
                "name": vehicle.auto_alias or vehicle.model_name or "BYD",
                "deviceType": "sensor", "driverKey": DRIVER_KEY,
                "driverInstanceId": self.settings.instance_id,
                "properties": {"manufacturer": "BYD", "model": vehicle.model_name,
                               "commandCatalog": command_catalog(car.capabilities, bool(self.settings.control_pin))},
            })

    async def refresh(self, identifier: str, *, force: bool = False) -> bool:
        if not force and time.monotonic() < self.next_refresh.get(identifier, 0):
            return False
        self.next_refresh[identifier] = time.monotonic() + self.settings.poll_seconds
        car = self.cars[identifier]
        try:
            realtime = await asyncio.wait_for(self.client.get_vehicle_realtime(car.vin), 60)
            hvac, gps = None, None
            if car.capabilities.climate is True:
                try:
                    hvac = await asyncio.wait_for(self.client.get_hvac_status(car.vin), 30)
                except Exception:
                    pass  # Missing auxiliary data stays unknown.
            if self.settings.include_location and car.capabilities.location is True:
                try:
                    gps = await asyncio.wait_for(self.client.get_gps_info(car.vin), 30)
                except Exception:
                    pass
            payload = state_payload(realtime, hvac, gps)
            await self.emit("STATE_UPDATE", identifier, payload)
            await self.emit("METRIC_UPDATE", identifier, {"metrics": [
                {"key": key, "value": sensor["value"], **({"unit": sensor["unit"]} if "unit" in sensor else {})}
                for key, sensor in payload["extras"]["sensors"].items() if sensor["value"] is not None
            ]})
            return True
        except Exception as error:
            await self.emit("STATE_UPDATE", identifier, {"extras": {
                "status": "unavailable", "stale": True, "error": error_message(error)}})
            return False

    async def poll(self) -> None:
        while True:
            async with self.io_lock:
                for identifier in self.cars:
                    await self.refresh(identifier)
            await asyncio.sleep(1)

    async def handle(self, message: dict[str, Any]) -> None:
        if str(message.get("event", message.get("type", ""))).upper() not in {"ACTION", "COMMAND_EXECUTE"}:
            return
        data = message.get("data", message.get("payload", {}))
        if not isinstance(data, dict):
            return
        identifier = message.get("device_id") or data.get("deviceId") or data.get("device_id")
        command = str(data.get("command", data.get("action", ""))).lower()
        request = data.get("requestId") or data.get("correlationId")
        request = request if isinstance(request, str) else None
        payload = data.get("payload", {})
        identity = json.dumps([identifier, command, payload], sort_keys=True)
        if request and request in self.results:
            previous, result = self.results[request]
            if previous == identity:
                await self.emit("ACTION_RESULT", str(identifier), result)
            else:
                await self.emit("ACTION_RESULT", str(identifier), {
                    "command": command, "requestId": request, "success": False,
                    "message": "Request identifier already used."})
            return
        result: dict[str, Any] = {"command": command, "success": False}
        for key in ("requestId", "correlationId"):
            if isinstance(data.get(key), str):
                result[key] = data[key]
        if request:
            # Keep the uncertainty result across a transport reconnect if a write is cancelled.
            result["message"] = "Command not confirmed. Check the car before retrying."
            self.results[request] = (identity, result)
            if len(self.results) > 256:
                self.results.popitem(last=False)
        started = time.monotonic()
        async with self.io_lock:
            try:
                if identifier not in self.cars:
                    result["message"] = "Choose a known car."
                elif not isinstance(payload, dict):
                    result["message"] = "Invalid command settings."
                elif command == "refresh_state":
                    if time.monotonic() < self.next_refresh.get(identifier, 0) - self.settings.poll_seconds + 30:
                        result["message"] = "Wait 30 seconds between refreshes."
                    else:
                        result["success"] = await self.refresh(identifier, force=True)
                        if not result["success"]:
                            result["message"] = "Can't refresh. Try again later."
                elif command not in {row["key"] for row in command_catalog(
                        self.cars[identifier].capabilities, bool(self.settings.control_pin))}:
                    result["message"] = "Control unavailable. Check the PIN and car capabilities."
                else:
                    climate_params = None
                    if command == "start_climate":
                        if set(payload) - {"temperature", "duration"}:
                            raise ValueError("Invalid command settings.")
                        temperature = payload.get("temperature", 21)
                        duration = payload.get("duration", 10)
                        if (type(temperature) not in {float, int} or not math.isfinite(temperature)
                                or not 15 <= temperature <= 31 or type(duration) is not int
                                or duration not in {10, 15, 20, 25, 30}):
                            raise ValueError("Invalid command settings.")
                        climate_params = ClimateStartParams(
                            temperature=temperature, time_span=minutes_to_time_span(duration))
                    elif payload:
                        raise ValueError("Invalid command settings.")
                    vin = self.cars[identifier].vin
                    # Verify for this exact vehicle before every write. Never retry a timed-out write.
                    await asyncio.wait_for(self.client.verify_command_access(vin), 30)
                    args = {"params": climate_params} if climate_params is not None else {}
                    response = await asyncio.wait_for(getattr(self.client, command)(vin, **args), 120)
                    result["success"] = response.success is True and response.control_state == ControlState.SUCCESS
                    if result["success"]:
                        result["stateRefresh"] = "updated" if await self.refresh(identifier, force=True) else "pending"
                    else:
                        result["message"] = "Command not confirmed. Check the car before retrying."
            except ValueError:
                result["message"] = "Invalid command settings."
            except Exception as error:
                result["message"] = error_message(error)
        result["latencyMs"] = round((time.monotonic() - started) * 1000)
        if result["success"]:
            result.pop("message", None)
        if request:
            self.results[request] = (identity, result)
            if len(self.results) > 256:
                self.results.popitem(last=False)
        await self.emit("ACTION_RESULT", str(identifier or self.settings.instance_id), result)


async def stdio(settings: Settings) -> None:
    async def emit(message: dict[str, Any]) -> None:
        print(json.dumps(message, allow_nan=False), flush=True)
    runtime = Runtime(settings, emit)
    poller = None
    try:
        await runtime.start()
        poller = asyncio.create_task(runtime.poll())
        while line := await asyncio.to_thread(sys.stdin.readline):
            try:
                message = json.loads(line)
                if isinstance(message, dict):
                    await runtime.handle(message)
            except (ValueError, TypeError):
                print("Invalid command message.", file=sys.stderr)
    finally:
        if poller:
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
        await runtime.close()


async def websocket(settings: Settings, stop: asyncio.Event) -> None:
    async with aiohttp.ClientSession() as session:
        backoff = 2
        results: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
        while not stop.is_set():
            runtime, tasks = None, []
            try:
                async with session.ws_connect(settings.controller_url, heartbeat=20, max_msg_size=65536) as ws:
                    await ws.send_json({"method": "driver.register", "protocolVersion": 1,
                                        "params": {"driverKey": DRIVER_KEY, "instanceId": settings.instance_id,
                                                   "name": "BYD Vehicle"}})
                    while True:
                        reply = await ws.receive_json(timeout=15)
                        if reply.get("event") == "REGISTERED" and reply.get("ok") is True:
                            break
                        if reply.get("ok") is False:
                            raise RuntimeError("Controller registration rejected.")
                    runtime = Runtime(settings, ws.send_json)
                    runtime.results = results
                    await runtime.start()
                    backoff = 2
                    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)

                    async def receive() -> None:
                        async for message in ws:
                            if message.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    data = json.loads(message.data)
                                    if isinstance(data, dict):
                                        await queue.put(data)
                                except ValueError:
                                    continue

                    async def commands() -> None:
                        while True:
                            await runtime.handle(await queue.get())

                    tasks = [asyncio.create_task(receive()), asyncio.create_task(commands()),
                             asyncio.create_task(runtime.poll()), asyncio.create_task(stop.wait())]
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        if not task.cancelled():
                            task.result()
            except Exception as error:
                print(error_message(error), file=sys.stderr)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if runtime:
                    await runtime.close()
            if not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), backoff)
                except TimeoutError:
                    pass
                backoff = min(900, backoff * 2)


async def main() -> None:
    parser = argparse.ArgumentParser(description="BYD developer driver for Haptique OS")
    parser.add_argument("--transport", choices=["websocket", "stdio"], default="websocket")
    parser.add_argument("--check", action="store_true", help="Check dependencies without contacting a car")
    args = parser.parse_args()
    if args.check:
        print(json.dumps({"driver": DRIVER_KEY, "version": VERSION, "pybyd": version("pybyd"),
                          "python": sys.version.split()[0], "protocolVersion": 1}))
        return
    settings = Settings.from_env()
    if args.transport == "stdio":
        await stdio(settings)
    else:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:
                pass
        await websocket(settings, stop)


if __name__ == "__main__":
    # Avoid upstream debug traces containing account/vehicle response data.
    logging.getLogger("pybyd").disabled = True
    logging.getLogger("pybyd").addHandler(logging.NullHandler())
    logging.getLogger().setLevel(logging.CRITICAL)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except ValueError as error:
        sys.exit(str(error))
    except Exception as error:
        sys.exit(error_message(error))
