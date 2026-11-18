"""Entity names live in translations/en.json and are found through translation_key."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "volvo_au"


def test_translation_files_agree_and_every_key_is_used():
    strings = json.loads((ROOT / "strings.json").read_text())
    shipped = json.loads((ROOT / "translations" / "en.json").read_text())
    assert strings == shipped
    for platform, entries in strings["entity"].items():
        source = (ROOT / f"{platform}.py").read_text()
        for key, value in entries.items():
            assert value["name"], (platform, key)
            used = f'"{key}"' in source or key.startswith(("tyre_", "charge_schedule_"))
            assert used, (platform, key)


def test_no_literal_entity_names_are_left_in_code():
    for platform in ("sensor", "binary_sensor", "switch", "lock", "number", "time", "device_tracker", "button"):
        source = (ROOT / f"{platform}.py").read_text()
        assert not re.search(r'\n\s+name="[^"]+",', source), platform
        assert "_attr_name =" not in source, platform


def test_reconfigure_keeps_the_entry_and_requires_the_same_car():
    source = (ROOT / "config_flow.py").read_text()
    step = source.split("async def async_step_reconfigure", 1)[1].split("async def _async_relogin", 1)[0]
    assert "_get_reconfigure_entry()" in step
    assert "async_set_unique_id" not in step
    assert '"vin_mismatch"' in source
