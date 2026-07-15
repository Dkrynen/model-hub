"""Versioned open-core plugin seam for LAC product extensions.

Plugins are Python packages exposing an entry point in the ``lac.plugins``
group. A plugin must declare ``host_api_version`` and provide a bounded
``product_state()`` before any CLI, API, or lifecycle hook is called.
"""
from __future__ import annotations

import importlib
import re
import sys
from dataclasses import dataclass
from datetime import date
from importlib.metadata import entry_points
from pathlib import Path

GROUP = "lac.plugins"
HOST_API_VERSION = 1
_PRODUCT_KEYS = {"schema_version", "product", "entitlement", "capabilities"}
_ENTITLEMENT_KEYS = {"state", "plan", "expires_human", "checked"}
_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _is_iso_date(value) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _metadata_string(value, *, limit: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if sum(2 if ord(char) > 0xFFFF else 1 for char in value) > limit:
        return None
    if any(ord(char) < 32 or 0x7F <= ord(char) <= 0x9F for char in value):
        return None
    return value


def _ensure_plugin_dir_on_path() -> None:
    """Make the bootstrap plugin directory visible before entry-point reads."""
    try:
        from backend import pro_install

        plugin_dir = Path(pro_install.PLUGIN_DIR)
        if not plugin_dir.is_dir():
            return
        path_str = str(plugin_dir)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
        importlib.invalidate_caches()
    except Exception:  # noqa: BLE001 - discovery plumbing must never break core
        return


def _entry_points():
    """Indirection so tests can substitute fake entry points."""
    return list(entry_points(group=GROUP))


@dataclass
class LoadedPlugin:
    name: str
    version: str
    obj: object | None
    error: str | None = None
    product_id: str | None = None
    host_api_version: int | None = None
    product_state: dict | None = None
    compatibility_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.state == "ready"

    @property
    def state(self) -> str:
        if self.error is not None:
            return "load_error"
        if self.compatibility_error is not None:
            return "incompatible"
        return "ready"


def _validate_product_state(value) -> dict | None:
    if not isinstance(value, dict) or set(value) != _PRODUCT_KEYS:
        return None
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return None
    if value.get("product") != "local_pro":
        return None
    entitlement = value.get("entitlement")
    if not isinstance(entitlement, dict) or set(entitlement) != _ENTITLEMENT_KEYS:
        return None
    entitlement_state = entitlement.get("state")
    if entitlement_state not in {"active", "inactive"}:
        return None
    plan = entitlement.get("plan")
    known_plan = plan in {"dev", "pro_local", "pro_cloud"}
    if plan is not None and not known_plan:
        return None
    expires = entitlement.get("expires_human")
    checked = entitlement.get("checked")
    if entitlement_state == "active":
        if not known_plan or not (expires == "while subscribed" or _is_iso_date(expires)):
            return None
    elif plan is not None or expires is not None or checked is not None:
        return None
    if checked is not None and not _is_iso_date(checked):
        return None
    capabilities = value.get("capabilities")
    if (
        not isinstance(capabilities, list)
        or len(capabilities) > 64
        or any(
            not isinstance(item, str) or _CAPABILITY_PATTERN.fullmatch(item) is None
            for item in capabilities
        )
        or capabilities != sorted(set(capabilities))
    ):
        return None
    return {
        "schema_version": 1,
        "product": "local_pro",
        "entitlement": {
            "state": entitlement["state"],
            "plan": plan,
            "expires_human": expires,
            "checked": checked,
        },
        "capabilities": list(capabilities),
    }


def discover() -> list[LoadedPlugin]:
    """Load and validate all ``lac.plugins`` entry points in isolation."""
    _ensure_plugin_dir_on_path()
    out: list[LoadedPlugin] = []
    for ep in _entry_points():
        entry_name = _metadata_string(getattr(ep, "name", None), limit=120) or "?"
        try:
            obj = ep.load()
            raw_name = getattr(obj, "name", None)
            raw_version = getattr(obj, "version", None)
            name_value = (
                entry_name
                if raw_name is None or (isinstance(raw_name, str) and raw_name == "")
                else raw_name
            )
            version_value = (
                "?"
                if raw_version is None or (isinstance(raw_version, str) and raw_version == "")
                else raw_version
            )
            name = _metadata_string(name_value, limit=120) or entry_name
            version = _metadata_string(version_value, limit=64) or "?"
            invalid_metadata = (
                _metadata_string(name_value, limit=120) is None
                or _metadata_string(version_value, limit=64) is None
            )
            product_id = getattr(obj, "product_id", None)
            host_api_version = getattr(obj, "host_api_version", None)
        except Exception as exc:  # noqa: BLE001 - a plugin must never break core
            out.append(LoadedPlugin(name=entry_name, version="?", obj=None, error=str(exc)))
            continue
        is_product_plugin = product_id is not None or name == "pro" or entry_name == "pro"
        safe_product_id = (
            product_id
            if isinstance(product_id, str) and product_id == "local_pro"
            else None
        )
        if invalid_metadata:
            out.append(LoadedPlugin(
                name=name,
                version=version,
                obj=obj,
                product_id=safe_product_id,
                host_api_version=host_api_version if type(host_api_version) is int else None,
                compatibility_error="invalid_plugin_metadata",
            ))
            continue
        if not is_product_plugin:
            out.append(LoadedPlugin(name=name, version=version, obj=obj))
            continue
        if product_id != "local_pro":
            out.append(LoadedPlugin(
                name=name,
                version=version,
                obj=obj,
                product_id=safe_product_id,
                host_api_version=host_api_version if type(host_api_version) is int else None,
                compatibility_error="product_id_mismatch",
            ))
            continue
        if type(host_api_version) is not int or host_api_version != HOST_API_VERSION:
            out.append(LoadedPlugin(
                name=name,
                version=version,
                obj=obj,
                product_id=product_id,
                host_api_version=host_api_version if type(host_api_version) is int else None,
                compatibility_error="host_api_version_mismatch",
            ))
            continue
        try:
            product_state_fn = getattr(obj, "product_state", None)
        except Exception:  # noqa: BLE001 - a descriptor must not break discovery
            out.append(LoadedPlugin(
                name=name,
                version=version,
                obj=obj,
                error="product_state_failed",
                product_id=product_id,
                host_api_version=host_api_version,
            ))
            continue
        if not callable(product_state_fn):
            out.append(LoadedPlugin(
                name=name,
                version=version,
                obj=obj,
                product_id=product_id,
                host_api_version=host_api_version,
                compatibility_error="product_state_missing",
            ))
            continue
        try:
            product_state = _validate_product_state(product_state_fn())
        except Exception:  # noqa: BLE001 - a product plugin must never break core
            out.append(LoadedPlugin(
                name=name,
                version=version,
                obj=obj,
                error="product_state_failed",
                product_id=product_id,
                host_api_version=host_api_version,
            ))
            continue
        if product_state is None:
            out.append(LoadedPlugin(
                name=name,
                version=version,
                obj=obj,
                product_id=product_id,
                host_api_version=host_api_version,
                compatibility_error="invalid_product_state",
            ))
            continue
        out.append(LoadedPlugin(
            name=name,
            version=version,
            obj=obj,
            product_id=product_id,
            host_api_version=host_api_version,
            product_state=product_state,
        ))
    return out
