"""Build an allowlisted reproducible ZIP accepted by HOS's local driver installer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = ["byd_vehicle.driver.json", "byd_vehicle.py", "requirements.txt"]


def build(output: Path | None = None) -> Path:
    manifest = json.loads((ROOT / "byd_vehicle.driver.json").read_text())
    output = output or ROOT / "dist" / f"haptique-byd-vehicle-{manifest['version']}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(ENTRIES):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (ROOT / name).read_bytes(), compresslevel=9)
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{checksum}  {output.name}\n")
    return output


if __name__ == "__main__":
    artifact = build()
    print(artifact)
    print(artifact.with_suffix(".zip.sha256").read_text().strip())
