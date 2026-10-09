"""Host/OS adapters: capability discovery, consent gates, privacy and lock controls."""

from jarvis.host.base import (
    ActionOutcome,
    AndroidHost,
    AppInfo,
    Capability,
    Device,
    HostAdapter,
    PosixHost,
    PrivacyMode,
    WindowsHost,
    detect_host,
)

__all__ = [
    "ActionOutcome",
    "AndroidHost",
    "AppInfo",
    "Capability",
    "Device",
    "HostAdapter",
    "PosixHost",
    "PrivacyMode",
    "WindowsHost",
    "detect_host",
]
