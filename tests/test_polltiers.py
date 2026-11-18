"""Slow-poll tiers (pure module, no Home Assistant needed)."""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).parents[1] / "custom_components" / "volvo_au"
_SPEC = importlib.util.spec_from_file_location("polltiers", ROOT / "polltiers.py")
polltiers = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(polltiers)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_value_is_fresh_until_its_tier_is_due():
    clock = Clock()
    tiers = polltiers.SlowTiers({"weather": 900.0}, clock)
    assert tiers.cached("weather") == (False, None)  # nothing yet: must read
    tiers.put("weather", {"temperature_c": 21})
    clock.t += 899
    assert tiers.cached("weather") == (True, {"temperature_c": 21})
    clock.t += 2
    fresh, stale = tiers.cached("weather")
    assert fresh is False and stale == {"temperature_c": 21}  # due, but a stale fallback is kept


def test_invalidate_forces_a_read():
    tiers = polltiers.SlowTiers({"target_soc": 900.0}, Clock())
    tiers.put("target_soc", 80)
    tiers.invalidate("target_soc", "unknown")
    assert tiers.cached("target_soc") == (False, None)
    assert not tiers.has("target_soc")


def test_only_listed_readers_are_slow():
    tiers = polltiers.SlowTiers()
    assert tiers.is_slow("software_info") and tiers.is_slow("weather")
    for fast in ("battery", "exterior", "parking_climatization", "health", "location"):
        assert not tiers.is_slow(fast)


def test_every_setter_invalidates_its_reader():
    src = (ROOT / "volvo_api.py").read_text()
    for reader in ("target_soc", "amp_limit", "global_charge_timer"):
        assert polltiers.SLOW_TTLS[reader]
        assert re.search(rf'self\._tiers\.invalidate\("{reader}"\)', src), reader
