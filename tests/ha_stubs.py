"""Load the Volvo integration's Home Assistant modules without Home Assistant installed.

Every ``homeassistant.*`` import is answered by a stub whose attributes are harmless placeholder classes, so
the platform modules (sensor, binary_sensor, switch, ...) can be imported and their entity tables inspected.
A few names the tests rely on are real: entity descriptions (dataclasses), the registry enums, the entity
categories and ``callback``.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

COMPONENT = Path(__file__).parents[1] / "custom_components" / "volvo_au"
PACKAGE = "_volvo_under_test"


class EntityCategory(enum.StrEnum):
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


class RegistryEntryDisabler(enum.StrEnum):
    INTEGRATION = "integration"
    USER = "user"


@dataclasses.dataclass(frozen=True, kw_only=True)
class EntityDescription:
    key: str
    translation_key: str | None = None
    entity_category: EntityCategory | None = None
    device_class: Any = None
    icon: str | None = None
    entity_registry_enabled_default: bool = True
    native_unit_of_measurement: Any = None
    state_class: Any = None
    suggested_display_precision: int | None = None
    suggested_unit_of_measurement: Any = None
    options: Any = None
    name: Any = None


class _AnyAttr(type):
    """Lets ``SensorDeviceClass.BATTERY`` style lookups on a placeholder class return a value."""

    def __getattr__(cls, name: str) -> Any:
        if not name.isupper():  # only enum-style names (DeviceClass.BATTERY) are invented
            raise AttributeError(name)
        return f"{cls.__name__}.{name}"


class _Placeholder(metaclass=_AnyAttr):
    """Base for every unknown Home Assistant class; accepts anything."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __class_getitem__(cls, item: Any) -> type:
        return cls


class FakeCoordinatorEntity(_Placeholder):
    def __init__(self, coordinator: Any = None, *args: Any, **kwargs: Any) -> None:
        self.coordinator = coordinator


class _AnyModule(types.ModuleType):
    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        if name.endswith("Description"):
            value: Any = EntityDescription
        elif name.isupper():
            value = name  # constants such as MATCH_ALL or PERCENTAGE
        else:
            value = type(name, (_Placeholder,), {})
        setattr(self, name, value)
        return value


def _install(monkeypatch: Any) -> None:
    names = (
        "homeassistant",
        "homeassistant.components",
        "homeassistant.components.binary_sensor",
        "homeassistant.components.button",
        "homeassistant.components.device_tracker",
        "homeassistant.components.lock",
        "homeassistant.components.number",
        "homeassistant.components.select",
        "homeassistant.components.sensor",
        "homeassistant.components.switch",
        "homeassistant.components.time",
        "homeassistant.config_entries",
        "homeassistant.const",
        "homeassistant.core",
        "homeassistant.exceptions",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
        "homeassistant.helpers.device_registry",
        "homeassistant.helpers.entity",
        "homeassistant.helpers.entity_platform",
        "homeassistant.helpers.entity_registry",
        "homeassistant.helpers.restore_state",
        "homeassistant.helpers.update_coordinator",
        "homeassistant.util",
        "homeassistant.util.dt",
    )
    for name in names:
        monkeypatch.setitem(sys.modules, name, _AnyModule(name))
    sys.modules["homeassistant.core"].callback = lambda func: func
    sys.modules["homeassistant.helpers.entity"].EntityCategory = EntityCategory
    sys.modules["homeassistant.helpers.entity_registry"].RegistryEntryDisabler = RegistryEntryDisabler
    sys.modules["homeassistant.helpers.update_coordinator"].CoordinatorEntity = FakeCoordinatorEntity
    sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = _Placeholder
    # `from homeassistant.helpers import entity_registry as er` needs the submodule as an attribute.
    sys.modules["homeassistant.helpers"].entity_registry = sys.modules["homeassistant.helpers.entity_registry"]


def load_volvo(monkeypatch: Any, registry: Any = None) -> types.SimpleNamespace:
    """Import the integration under a private package name; returns the loaded modules by name."""
    _install(monkeypatch)
    if registry is not None:
        sys.modules["homeassistant.helpers.entity_registry"].async_get = lambda hass: registry
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(COMPONENT)]
    monkeypatch.setitem(sys.modules, PACKAGE, package)

    # The network client needs aiohttp, httpx and cryptography; the entity code only needs its names.
    class VolvoApiError(Exception):
        pass

    class VolvoAuthError(VolvoApiError):
        pass

    monkeypatch.setitem(
        sys.modules,
        f"{PACKAGE}.volvo_api",
        types.SimpleNamespace(VolvoApiError=VolvoApiError, VolvoAuthError=VolvoAuthError, VolvoClient=object),
    )

    modules: dict[str, Any] = {}

    def load(name: str) -> Any:
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", COMPONENT / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, f"{PACKAGE}.{name}", module)
        spec.loader.exec_module(module)
        modules[name] = module
        return module

    for name in ("const", "livestream", "polltiers", "powertrain", "climate", "journal", "schedule", "online",
                 "coordinator", "entity", "button", "sensor", "binary_sensor", "switch", "number", "select", "time"):
        load(name)
    return types.SimpleNamespace(**modules)
