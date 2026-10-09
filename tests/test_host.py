"""Host adapters, consent gates, privacy and lock (spec sections 15, 18, 20, 21)."""

from __future__ import annotations

import pytest

from jarvis.errors import CapabilityUnavailable, ConsentRequired
from jarvis.host import (
    AndroidHost,
    Device,
    PosixHost,
    PrivacyMode,
    WindowsHost,
    detect_host,
)


class CaptureHost(PosixHost):
    """A host that implements capture, so the consent gate can be exercised.

    PosixHost deliberately ships no camera or microphone backend, which means its
    honest answer is CapabilityUnavailable - that is asserted separately below.
    """

    supported = PosixHost.supported + ("camera", "microphone", "screen", "screenshot", "ui_control")

    def click(self, description):
        self._require("ui_control")
        from jarvis.host import ActionOutcome

        return ActionOutcome(True, f"clicked {description}")

    def screenshot(self):
        self._require("screenshot")
        self._require_consent(Device.SCREEN)
        from jarvis.host import ActionOutcome

        return ActionOutcome(True, "captured")


@pytest.fixture()
def locked_down() -> PosixHost:
    return PosixHost(computer_use_enabled=False)


@pytest.fixture()
def enabled() -> CaptureHost:
    return CaptureHost(computer_use_enabled=True)


# ------------------------------------------------------------- capability gates
def test_everything_is_refused_while_computer_use_is_off(locked_down):
    with pytest.raises(CapabilityUnavailable):
        locked_down.list_apps()
    with pytest.raises(CapabilityUnavailable):
        locked_down.open_app("anything")


def test_capability_listing_explains_why_something_is_off(locked_down):
    caps = {c.name: c for c in locked_down.capabilities()}
    assert caps["list_apps"].available is False
    assert "disabled in settings" in caps["list_apps"].reason


def test_unsupported_capabilities_say_so():
    host = AndroidHost(computer_use_enabled=True)
    caps = {c.name: c for c in host.capabilities()}
    assert caps["ui_control"].available is False
    assert "not implemented" in caps["ui_control"].reason


def test_enabled_host_can_discover_applications(enabled):
    assert isinstance(enabled.list_apps(), list)


def test_describe_reports_the_platform_honestly(enabled):
    described = enabled.describe()
    assert described["platform"] == "linux"
    assert described["computer_use_enabled"] is True
    assert "capabilities" in described


# --------------------------------------------------------------------- consent
def test_camera_needs_explicit_consent(enabled):
    with pytest.raises(ConsentRequired) as excinfo:
        enabled.camera_on()
    assert "consent" in str(excinfo.value).lower()


def test_camera_works_after_consent_and_stays_visible(enabled):
    enabled.consented_devices.add(Device.CAMERA.value)
    outcome = enabled.camera_on()
    assert outcome.ok
    assert enabled.camera_active is True
    assert "CAMERA ACTIVE" in outcome.message
    assert "indicator remains visible" in outcome.message


def test_camera_can_always_be_turned_off(enabled):
    enabled.camera_off()
    assert enabled.camera_active is False


def test_microphone_needs_consent_too(enabled):
    with pytest.raises(ConsentRequired):
        enabled.mic_on()
    enabled.consented_devices.add(Device.MICROPHONE.value)
    assert enabled.mic_on().ok


# --------------------------------------------------------------- privacy mode
def test_privacy_mode_disables_capture(enabled):
    enabled.consented_devices.update({Device.CAMERA.value, Device.MICROPHONE.value})
    enabled.camera_on()
    enabled.mic_on()
    enabled.privacy_on()
    assert enabled.privacy_mode is PrivacyMode.PRIVACY
    assert enabled.camera_active is False
    assert enabled.mic_active is False


def test_privacy_mode_blocks_new_capture(enabled):
    enabled.consented_devices.add(Device.CAMERA.value)
    enabled.privacy_on()
    with pytest.raises(ConsentRequired):
        enabled.camera_on()


def test_privacy_mode_is_reversible(enabled):
    enabled.privacy_on()
    enabled.privacy_off()
    assert enabled.privacy_mode is PrivacyMode.NORMAL


def test_lock_pauses_the_agent_and_stops_capture(enabled):
    enabled.consented_devices.add(Device.CAMERA.value)
    enabled.camera_on()
    outcome = enabled.lock()
    assert enabled.privacy_mode is PrivacyMode.LOCKED
    assert enabled.camera_active is False
    assert "LOCKED" in outcome.message or "paused" in outcome.message.lower()


def test_nothing_works_while_locked(enabled):
    enabled.lock()
    with pytest.raises(ConsentRequired):
        enabled.list_apps()


def test_unlock_resumes_the_agent(enabled):
    enabled.lock()
    assert enabled.unlock_agent().ok
    assert enabled.privacy_mode is PrivacyMode.NORMAL
    assert isinstance(enabled.list_apps(), list)


# ------------------------------------------------------------------ file safety
def test_path_traversal_is_blocked(enabled, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    allowed, _ = enabled.file_operations_allowed(str(root / "a.txt"), str(root))
    assert allowed
    blocked, reason = enabled.file_operations_allowed(str(tmp_path / "secret.txt"), str(root))
    assert not blocked
    assert "outside the allowed root" in reason


def test_the_root_itself_is_allowed(enabled, tmp_path):
    allowed, _ = enabled.file_operations_allowed(str(tmp_path), str(tmp_path))
    assert allowed


# ------------------------------------------------------------------ autostart
def test_autostart_instructions_are_available_when_enabled(enabled):
    text = enabled.autostart_instructions()
    assert "autostart" in text.lower() or "systemd" in text.lower()


def test_autostart_instructions_are_documentation_not_an_action(locked_down):
    """Knowing how is not the same as doing it: the text is available even with
    computer use off, and it says plainly that Jarvis will not change anything."""
    text = locked_down.autostart_instructions()
    assert "will not change anything" in text
    assert "autostart" in text.lower()


# ------------------------------------------------------------------- adapters
def test_windows_adapter_reports_its_platform():
    assert WindowsHost().platform == "windows"
    assert "open_app" in WindowsHost().supported


def test_android_adapter_is_honest_about_its_sandbox():
    host = AndroidHost(computer_use_enabled=True)
    assert host.supported == ("file_operations",)
    with pytest.raises(CapabilityUnavailable):
        host.open_app("anything")


def test_android_autostart_mentions_termux():
    host = AndroidHost(computer_use_enabled=True)
    assert "termux" in host.autostart_instructions().lower()


def test_detect_host_returns_an_adapter():
    host = detect_host()
    assert host.platform in {"linux", "windows", "android"}


def test_detect_host_honours_the_computer_use_switch():
    assert detect_host(computer_use_enabled=False).computer_use_enabled is False
    assert detect_host(computer_use_enabled=True).computer_use_enabled is True


def test_ui_control_works_when_the_capability_exists(enabled):
    assert enabled.click("the OK button").ok


def test_an_unimplemented_action_says_so_rather_than_pretending(enabled):
    outcome = enabled.type_text("hello")
    assert outcome.ok is False
    assert outcome.message, "a refusal must explain itself"


def test_the_shipped_posix_host_has_no_capture_backend():
    """Honest unavailability, not a fake success."""
    host = PosixHost(computer_use_enabled=True)
    with pytest.raises(CapabilityUnavailable):
        host.camera_on()
    with pytest.raises(CapabilityUnavailable):
        host.screenshot()


def test_screenshot_needs_screen_consent(enabled):
    with pytest.raises(ConsentRequired):
        enabled.screenshot()
    enabled.consented_devices.add(Device.SCREEN.value)
    assert enabled.screenshot().ok
