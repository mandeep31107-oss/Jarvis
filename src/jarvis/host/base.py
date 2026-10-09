"""Host / OS adapter layer (spec sections 15, 18, 20, 21).

Every action that touches the user's machine goes through a :class:`HostAdapter`,
which declares honestly what it can and cannot do on this platform. An
unavailable capability raises ``CapabilityUnavailable`` rather than silently
doing nothing, and a capability that needs consent raises ``ConsentRequired``.

Nothing in this layer bypasses OS security. Where an OS permission is required
(camera, accessibility, automation) Jarvis asks for it through the OS's own
mechanism and records the answer.
"""

from __future__ import annotations

import enum
import os
import platform as _platform
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.errors import CapabilityUnavailable, ConsentRequired


class Device(str, enum.Enum):
    CAMERA = "camera"
    MICROPHONE = "microphone"
    SCREEN = "screen"


class PrivacyMode(str, enum.Enum):
    NORMAL = "normal"
    PRIVACY = "privacy"  # capture disabled, agent still answering
    LOCKED = "locked"  # agent paused; only unlock and emergency stop respond


@dataclass
class Capability:
    name: str
    available: bool
    reason: str = ""
    needs_consent: bool = False
    #: OS-level permission the user must grant, if any.
    os_permission: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "reason": self.reason,
            "needs_consent": self.needs_consent,
            "os_permission": self.os_permission,
        }


@dataclass
class AppInfo:
    name: str
    running: bool
    launch_command: str | None = None
    notes: str = ""


@dataclass
class ActionOutcome:
    ok: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class HostAdapter:
    """The interface a platform implementation provides.

    The default implementations here are the *safe* ones: they refuse. Platform
    subclasses opt in explicitly, one capability at a time.
    """

    platform: str = "unknown"
    #: Capabilities this adapter can ever provide, before consent is considered.
    supported: tuple[str, ...] = ()

    def __init__(self, *, computer_use_enabled: bool = False, consented_devices: Sequence[str] = ()) -> None:
        self.computer_use_enabled = computer_use_enabled
        self.consented_devices: set[str] = {d.lower() for d in consented_devices}
        self.privacy_mode = PrivacyMode.NORMAL
        self.camera_active = False
        self.mic_active = False

    # --- discovery -----------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "system": _platform.system(),
            "release": _platform.release(),
            "machine": _platform.machine(),
            "python": _platform.python_version(),
            "computer_use_enabled": self.computer_use_enabled,
            "privacy_mode": self.privacy_mode.value,
            "camera_active": self.camera_active,
            "mic_active": self.mic_active,
            "consented_devices": sorted(self.consented_devices),
            "capabilities": [c.as_dict() for c in self.capabilities()],
        }

    def capabilities(self) -> list[Capability]:
        out = []
        for name in (
            "open_app", "close_app", "list_apps", "file_operations", "ui_control",
            "keyboard_shortcuts", "screenshot", "camera", "microphone", "lock_device",
            "autostart",
        ):
            supported = name in self.supported
            out.append(
                Capability(
                    name=name,
                    available=supported and self.computer_use_enabled,
                    reason=(
                        "supported by this platform but computer use is disabled in settings"
                        if supported and not self.computer_use_enabled
                        else "" if supported else f"not implemented for {self.platform}"
                    ),
                    needs_consent=name in {"camera", "microphone", "screen", "screenshot", "lock_device"},
                    os_permission={
                        "camera": "Camera permission for your terminal/host app",
                        "microphone": "Microphone permission for your terminal/host app",
                        "screen": "Screen recording permission",
                        "screenshot": "Screen recording permission",
                        "ui_control": "Accessibility permission",
                        "lock_device": "OS lock screen",
                    }.get(name, ""),
                )
            )
        return out

    def has(self, name: str) -> bool:
        return name in self.supported and self.computer_use_enabled

    # --- guards --------------------------------------------------------------
    def _require(self, capability: str) -> None:
        if self.privacy_mode is PrivacyMode.LOCKED:
            raise ConsentRequired("Agent is locked. Unlock first - nothing else responds.")
        if self.privacy_mode is PrivacyMode.PRIVACY and capability in {
            "camera", "microphone", "screen", "screenshot"
        }:
            raise ConsentRequired("Privacy mode is on; capture is disabled.")
        if not self.has(capability):
            raise CapabilityUnavailable(
                f"'{capability}' is not available on {self.platform}"
                + ("" if self.computer_use_enabled else " (computer use is disabled in settings)")
            )

    def _require_consent(self, device: Device) -> None:
        if device.value not in self.consented_devices:
            raise ConsentRequired(
                f"{device.value} access needs your explicit consent and none has been given. "
                "Consent is revocable at any time - Jarvis never enables a capture device on "
                "its own."
            )

    # --- applications --------------------------------------------------------
    def list_apps(self) -> list[AppInfo]:
        self._require("list_apps")
        return []

    def open_app(self, name: str) -> ActionOutcome:
        self._require("open_app")
        return ActionOutcome(False, f"open_app is not implemented on {self.platform}")

    def close_app(self, name: str) -> ActionOutcome:
        self._require("close_app")
        return ActionOutcome(False, f"close_app is not implemented on {self.platform}")

    # --- UI control ----------------------------------------------------------
    def click(self, description: str) -> ActionOutcome:
        self._require("ui_control")
        return ActionOutcome(False, "UI control requires a platform automation backend")

    def type_text(self, text: str) -> ActionOutcome:
        self._require("ui_control")
        return ActionOutcome(False, "UI control requires a platform automation backend")

    def shortcut(self, keys: str) -> ActionOutcome:
        self._require("keyboard_shortcuts")
        return ActionOutcome(False, "Shortcut injection is not implemented here")

    def screenshot(self) -> ActionOutcome:
        self._require("screenshot")
        self._require_consent(Device.SCREEN)
        return ActionOutcome(False, "Screenshot capture is not implemented here")

    # --- capture -------------------------------------------------------------
    def camera_on(self) -> ActionOutcome:
        """Turn the camera on. The OS's own indicator stays on - Jarvis never hides it."""
        self._require("camera")
        self._require_consent(Device.CAMERA)
        self.camera_active = True
        return ActionOutcome(True, "CAMERA ACTIVE - the OS camera indicator remains visible.")

    def camera_off(self) -> ActionOutcome:
        self.camera_active = False
        return ActionOutcome(True, "CAMERA OFF")

    def mic_on(self) -> ActionOutcome:
        self._require("microphone")
        self._require_consent(Device.MICROPHONE)
        self.mic_active = True
        return ActionOutcome(True, "MICROPHONE ACTIVE")

    def mic_off(self) -> ActionOutcome:
        self.mic_active = False
        return ActionOutcome(True, "MICROPHONE OFF")

    # --- privacy / lock ------------------------------------------------------
    def privacy_on(self) -> ActionOutcome:
        self.privacy_mode = PrivacyMode.PRIVACY
        self.camera_off()
        self.mic_off()
        return ActionOutcome(True, "PRIVACY MODE - capture disabled, agent still answering.")

    def privacy_off(self) -> ActionOutcome:
        self.privacy_mode = PrivacyMode.NORMAL
        return ActionOutcome(True, "Privacy mode off.")

    def lock(self) -> ActionOutcome:
        """Pause the agent and hand control back to the OS lock screen.

        Jarvis never locks *another* person out and never disables a security
        control - it only triggers the normal lock and stops its own work.
        """
        self.privacy_mode = PrivacyMode.LOCKED
        self.camera_off()
        self.mic_off()
        outcome = self._os_lock()
        return ActionOutcome(
            outcome.ok,
            ("LOCKED - agent paused. " if outcome.ok else "Agent paused; OS lock failed: ")
            + outcome.message,
            data=outcome.data,
        )

    def unlock_agent(self) -> ActionOutcome:
        """Returns the agent to normal mode. Identity-sensitive actions still need
        real authentication - this only lifts Jarvis's own pause."""
        self.privacy_mode = PrivacyMode.NORMAL
        return ActionOutcome(True, "Agent resumed.")

    def _os_lock(self) -> ActionOutcome:
        return ActionOutcome(False, f"no lock command implemented for {self.platform}")

    # --- files ---------------------------------------------------------------
    def file_operations_allowed(self, path: str | Path, root: str | Path) -> tuple[bool, str]:
        """Path-traversal guard used by every file action."""
        try:
            target = Path(path).resolve()
            allowed_root = Path(root).resolve()
        except OSError as exc:
            return False, f"cannot resolve path: {exc}"
        if allowed_root not in target.parents and target != allowed_root:
            return False, f"{target} is outside the allowed root {allowed_root}"
        return True, ""

    def autostart_instructions(self) -> str:
        """How to start Jarvis with the OS.

        This is documentation, not an action, so it is available even when
        computer use is disabled - knowing how is not the same as doing it.
        Jarvis never installs itself as a startup item without asking.
        """
        body = self._autostart_text()
        if not self.computer_use_enabled:
            body = (
                "Computer use is disabled in settings, so Jarvis will not change anything "
                "for you. The steps below are for you to apply yourself.\n\n" + body
            )
        return body

    def _autostart_text(self) -> str:
        return "No autostart instructions for this platform."


class PosixHost(HostAdapter):
    """Linux / *BSD. Uses freedesktop tooling where present."""

    platform = "linux"
    supported = ("open_app", "close_app", "list_apps", "file_operations", "lock_device", "autostart")

    def list_apps(self) -> list[AppInfo]:
        self._require("list_apps")
        apps: list[AppInfo] = []
        for directory in ("/usr/share/applications", "/usr/local/share/applications",
                          str(Path.home() / ".local/share/applications")):
            folder = Path(directory)
            if not folder.is_dir():
                continue
            for desktop in sorted(folder.glob("*.desktop")):
                name = desktop.stem
                apps.append(AppInfo(name=name, running=False, launch_command=f"gtk-launch {name}"))
        return apps

    def open_app(self, name: str) -> ActionOutcome:
        self._require("open_app")
        for launcher in ("gtk-launch", "gio launch", "xdg-open"):
            binary = launcher.split()[0]
            if shutil.which(binary):
                try:
                    proc = subprocess.run(
                        launcher.split() + [name], capture_output=True, text=True, timeout=15
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    return ActionOutcome(False, f"could not launch {name}: {exc}")
                return ActionOutcome(proc.returncode == 0, proc.stderr.strip() or f"launched {name}")
        return ActionOutcome(False, "no application launcher found (tried gtk-launch, gio, xdg-open)")

    def close_app(self, name: str) -> ActionOutcome:
        self._require("close_app")
        if not shutil.which("pkill"):
            return ActionOutcome(False, "pkill not available")
        proc = subprocess.run(["pkill", "-f", name], capture_output=True, text=True)
        return ActionOutcome(proc.returncode in (0, 1), f"pkill -f {name} returned {proc.returncode}")

    def _os_lock(self) -> ActionOutcome:
        for command in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"], ["gnome-screensaver-command", "-l"]):
            if shutil.which(command[0]):
                proc = subprocess.run(command, capture_output=True, text=True)
                return ActionOutcome(proc.returncode == 0, " ".join(command))
        return ActionOutcome(False, "no known screen locker installed")

    def _autostart_text(self) -> str:
        return """Create ~/.config/autostart/jarvis.desktop:

    [Desktop Entry]
    Type=Application
    Name=Jarvis
    Exec=/path/to/jarvis serve
    X-GNOME-Autostart-enabled=true

Or install a systemd user unit and enable lingering:

    systemctl --user enable --now jarvis.service
    loginctl enable-linger $USER
"""


class WindowsHost(HostAdapter):
    """Windows. UI control needs the user to enable it; nothing here bypasses UAC."""

    platform = "windows"
    supported = ("open_app", "close_app", "list_apps", "file_operations", "lock_device", "autostart")

    def list_apps(self) -> list[AppInfo]:
        self._require("list_apps")
        # Windows environment variable names are case-insensitive, and the
        # canonical spelling really is mixed-case - do not "fix" these to
        # upper case, they are the documented names.
        paths = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")),  # noqa: SIM112
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),  # noqa: SIM112
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        ]
        apps: list[AppInfo] = []
        for root in paths:
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if child.is_dir():
                    apps.append(AppInfo(name=child.name, running=False, launch_command=str(child)))
        return apps

    def open_app(self, name: str) -> ActionOutcome:
        self._require("open_app")
        try:
            os.startfile(name)  # type: ignore[attr-defined]
        except OSError as exc:
            return ActionOutcome(False, f"could not open {name}: {exc}")
        except AttributeError:
            return ActionOutcome(False, "os.startfile is only available on Windows")
        return ActionOutcome(True, f"opened {name}")

    def close_app(self, name: str) -> ActionOutcome:
        self._require("close_app")
        proc = subprocess.run(["taskkill", "/IM", name, "/T"], capture_output=True, text=True)
        return ActionOutcome(proc.returncode == 0, proc.stdout.strip() or proc.stderr.strip())

    def _os_lock(self) -> ActionOutcome:
        proc = subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], capture_output=True, text=True)
        return ActionOutcome(proc.returncode == 0, "rundll32 user32.dll,LockWorkStation")

    def _autostart_text(self) -> str:
        return """Option 1 - Startup folder:
    shell:startup   ->  drop a shortcut to `jarvis serve` there.

Option 2 - Task Scheduler (runs at logon, survives reboots):
    schtasks /Create /TN "Jarvis" /SC ONLOGON /TR "\\"C:\\path\\to\\python.exe\\" -m jarvis serve"

Option 3 - Windows service (runs without a logged-in user):
    sc create Jarvis binPath= "C:\\path\\to\\jarvis-service.exe" start= auto

Note: an always-on agent is a standing security decision. Prefer ONLOGON over a
service so it only runs while you are signed in.
"""


class AndroidHost(HostAdapter):
    """Android, typically under Termux.

    Android's sandbox means Jarvis cannot drive other apps' UIs the way it can on
    a desktop. The honest answer is: use the Accessibility framework via an app
    the user installs and grants, or drive the device over ADB with the user's
    explicit authorisation. Neither is done silently here.
    """

    platform = "android"
    supported = ("file_operations",)

    def _autostart_text(self) -> str:
        return """Termux:
    pkg install termux-services termux-boot
    mkdir -p ~/.termux/boot
    printf '#!/data/data/com.termux/files/usr/bin/sh\\njarvis serve &\\n' > ~/.termux/boot/jarvis
    chmod +x ~/.termux/boot/jarvis

Android will still apply its own battery restrictions - disable them for Termux,
or the process will be killed in the background.
"""


def detect_host(**kwargs: Any) -> HostAdapter:
    """Pick the adapter for the current OS."""
    system = _platform.system().lower()
    if "ANDROID_ROOT" in os.environ or "com.termux" in os.environ.get("PREFIX", ""):
        return AndroidHost(**kwargs)
    if system.startswith("win"):
        return WindowsHost(**kwargs)
    if system == "darwin":
        return PosixHost(**kwargs)
    return PosixHost(**kwargs)
