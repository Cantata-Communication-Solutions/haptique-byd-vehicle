import hashlib
import json
import subprocess
import sys
import zipfile

from scripts.build_package import ENTRIES, ROOT, build


def test_package_reproducible_allowlist_and_installer_shape(tmp_path):
    first = build(tmp_path / "first.zip")
    second = build(tmp_path / "second.zip")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert sorted(archive.namelist()) == sorted(ENTRIES)
        assert set(ENTRIES) == {"byd_vehicle.driver.json", "byd_vehicle.py", "requirements.txt"}
        assert b"Copyright (c) 2026 jkaberg" in archive.read("byd_vehicle.py")
        assert b"Permission is hereby granted" in archive.read("byd_vehicle.py")
        assert len([name for name in archive.namelist() if name.endswith(".py")]) == 1
        assert len([name for name in archive.namelist() if name.endswith(".driver.json")]) == 1
        manifest = json.loads(archive.read("byd_vehicle.driver.json"))
        assert manifest["driverFile"] == "byd_vehicle.py"
        assert manifest["driverType"] == "python"
        assert manifest["key"] == "BYD_VEHICLE"
        assert manifest["iotClass"] == "cloud_polling"
        assert "haptiqueApp" not in manifest  # Community package makes no protected-core claims.
        assert b"pybyd==0.0.77" in archive.read("requirements.txt")
    assert hashlib.sha256(first.read_bytes()).hexdigest() in first.with_suffix(".zip.sha256").read_text()


def test_registry_points_to_unsigned_release_and_remains_community(tmp_path):
    listing = json.loads((ROOT / "registry/com.haptique.community.byd-vehicle.json").read_text())
    manifest = json.loads((ROOT / "byd_vehicle.driver.json").read_text())
    assert listing["version"] == manifest["version"]
    assert listing["trustLevel"] == "community"
    artifact = listing["artifact"]
    assert artifact["downloadUrl"].endswith(f"/v{manifest['version']}/haptique-byd-vehicle-{manifest['version']}.zip")
    assert artifact["sha256"] == hashlib.sha256(build(tmp_path / "registry.zip").read_bytes()).hexdigest()
    assert "signature" not in artifact
    assert "signingKeyId" not in artifact
    assert listing["driver"]["key"] == manifest["key"]
    assert "credential_storage" in listing["permissions"]


def test_dependencies_really_import_and_cli_offline_check():
    result = subprocess.run([sys.executable, str(ROOT / "byd_vehicle.py"), "--check"],
                            capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    assert data["pybyd"] == "0.0.77"
    assert data["protocolVersion"] == 1


def test_missing_config_process_fails_cleanly_without_network():
    import os
    env = {key: value for key, value in os.environ.items() if not key.startswith("BYD_")}
    result = subprocess.run([sys.executable, str(ROOT / "byd_vehicle.py")],
                            capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode != 0
    assert not result.stdout
    assert result.stderr.strip() == "Enter your BYD account and password."
    assert "Traceback" not in result.stderr
