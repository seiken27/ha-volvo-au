"""Live update streams: the policy that decides whether a service really pushes updates."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "volvo_au"
_SPEC = importlib.util.spec_from_file_location("livestream", ROOT / "livestream.py")
livestream = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(livestream)


def test_a_one_line_snapshot_that_closes_at_once_is_given_up_on():
    policy = livestream.StreamPolicy()
    assert policy.connection_ended(lines=1, seconds=0.4) is None
    assert policy.snapshot_only is True and policy.proven is False


def test_a_second_line_proves_the_stream_and_it_reconnects_after_a_pause():
    policy = livestream.StreamPolicy()
    assert policy.connection_ended(lines=5, seconds=30) == livestream.BACKOFF_START
    assert policy.proven is True and policy.snapshot_only is False


def test_a_long_quiet_connection_also_proves_the_stream():
    policy = livestream.StreamPolicy()
    assert policy.connection_ended(lines=1, seconds=300) == livestream.BACKOFF_START
    assert policy.proven is True


def test_once_proven_a_short_session_is_retried_not_dropped_and_backoff_grows():
    policy = livestream.StreamPolicy()
    policy.connection_ended(lines=3, seconds=500)
    first = policy.connection_failed()
    second = policy.connection_failed()
    assert second == min(first * 2, livestream.BACKOFF_MAX)
    assert policy.connection_ended(lines=1, seconds=2) is not None  # proven: keep trying


def test_backoff_is_capped_and_resets_after_a_healthy_session():
    policy = livestream.StreamPolicy()
    for _ in range(20):
        delay = policy.connection_failed()
    assert delay == livestream.BACKOFF_MAX
    assert policy.connection_ended(lines=10, seconds=600) == livestream.BACKOFF_START  # start over


def test_streams_cover_the_state_services_the_poll_reads():
    assert set(livestream.STREAMS) == {
        "battery", "odometer", "exterior", "availability", "parking_climatization", "pre_cleaning"
    }
    assert all(path.startswith("/services.vehiclestates.") and "/Get" in path for path in livestream.STREAMS.values())


def test_stream_is_wired_into_setup_options_and_unload():
    init = (ROOT / "__init__.py").read_text()
    assert "await coordinator.async_start_streams()" in init
    assert "entry.async_on_unload(coordinator.async_stop_streams)" in init
    coordinator = (ROOT / "coordinator.py").read_text()
    assert "self._adapt_interval(data)" in coordinator  # a pushed frame can change the poll speed too
    assert "CONF_LIVE_UPDATES" in (ROOT / "config_flow.py").read_text()
