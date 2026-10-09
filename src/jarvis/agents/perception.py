"""Computer-use agent (spec section 15) and vision agent (sections 17, 18).

Both are thin, honest layers over the host adapter. Neither pretends to have
access it does not have: if the capability is missing, unconsented, or blocked by
privacy mode, the agent says so and returns options instead of a fabricated
result.

Vision is additionally constrained by section 17: it will not infer health,
religion, political affiliation, sexual orientation, criminality or other
sensitive attributes about people, and it says so when asked.
"""

from __future__ import annotations

from typing import Any

from jarvis.agents.base import Agent, AgentRequest, AgentResult
from jarvis.core.confidence import Claim, Confidence
from jarvis.core.options import Dilemma, Option
from jarvis.core.risk import RiskLevel
from jarvis.host import HostAdapter, PrivacyMode, detect_host

#: Inferences the vision agent refuses to make about people (spec section 17).
PROHIBITED_INFERENCES = (
    "health condition or diagnosis",
    "religion or belief",
    "political affiliation",
    "sexual orientation",
    "gender identity",
    "ethnicity or national origin",
    "criminal behaviour or likelihood of it",
    "creditworthiness or financial status",
    "employability or intelligence",
    "biometric identity of a specific person",
)


class ComputerAgent(Agent):
    name = "computer"
    description = "Controls authorized applications through the OS, within its security boundaries."
    capabilities = ("computer_use", "apps", "files", "ui", "privacy", "lock")

    def __init__(self, runtime: Any = None, *, host: HostAdapter | None = None) -> None:
        super().__init__(runtime)
        self.host = host or detect_host()

    def run(self, request: AgentRequest) -> AgentResult:
        intent = (request.param("intent") or request.text or "").strip().lower()
        result = AgentResult(agent=self.name, summary=f"Computer request: {intent or 'status'}")

        if not intent or intent in {"status", "capabilities", "what can you do"}:
            result.data["host"] = self.host.describe()
            result.summary = (
                f"Host {self.host.platform}: "
                f"{sum(1 for c in self.host.capabilities() if c.available)} capability(ies) available, "
                f"privacy mode {self.host.privacy_mode.value}."
            )
            result.add_claim(
                Claim.certain(
                    f"Running on {self.host.platform}; computer use is "
                    f"{'enabled' if self.host.computer_use_enabled else 'disabled in settings'}."
                )
            )
            unavailable = [c.name for c in self.host.capabilities() if not c.available]
            if unavailable:
                result.follow_ups.append(
                    "Not available right now: " + ", ".join(unavailable[:8])
                )
            return result

        target = request.param("target") or ""
        action_map = {
            "open": self.host.open_app,
            "close": self.host.close_app,
            "lock": lambda _t="": self.host.lock(),
            "privacy_on": lambda _t="": self.host.privacy_on(),
            "privacy_off": lambda _t="": self.host.privacy_off(),
            "camera_on": lambda _t="": self.host.camera_on(),
            "camera_off": lambda _t="": self.host.camera_off(),
            "mic_on": lambda _t="": self.host.mic_on(),
            "mic_off": lambda _t="": self.host.mic_off(),
        }
        handler = action_map.get(intent)
        if handler is None:
            result.ok = False
            result.summary = (
                f"Unknown computer action '{intent}'. Known: {', '.join(sorted(action_map))}."
            )
            return result

        from jarvis.errors import CapabilityUnavailable, ConsentRequired

        try:
            outcome = handler(target) if intent in {"open", "close"} else handler()
        except ConsentRequired as exc:
            result.ok = False
            result.summary = f"Consent required: {exc}"
            result.add_claim(Claim.certain(str(exc)))
            result.dilemmas.append(self._consent_dilemma(intent, str(exc)))
            return result
        except CapabilityUnavailable as exc:
            result.ok = False
            result.summary = f"Not available: {exc}"
            result.add_claim(Claim.certain(str(exc)))
            result.follow_ups.extend(
                [
                    "Enable computer use in settings if you want Jarvis to drive applications.",
                    "Or do this step manually - Jarvis can prepare everything up to it.",
                ]
            )
            return result

        result.ok = outcome.ok
        result.summary = outcome.message
        result.data["outcome"] = {"ok": outcome.ok, "message": outcome.message, "data": outcome.data}
        result.add_claim(Claim.certain(outcome.message))
        if intent in {"camera_on", "mic_on"}:
            result.add_claim(
                Claim.certain(
                    "The OS's own capture indicator stays visible. Jarvis never records covertly "
                    "and never hides the indicator."
                )
            )
        return result

    def _consent_dilemma(self, intent: str, reason: str) -> Dilemma:
        dilemma = Dilemma(
            situation=f"You asked Jarvis to '{intent}'.",
            problem=reason,
            risk="Enabling capture without consent would breach privacy expectations and OS policy.",
        )
        dilemma.add(
            Option(
                label="A",
                title="Grant the permission explicitly",
                kind="safe",
                expected_result=f"'{intent}' works, and the OS indicator stays visible.",
                risk=RiskLevel.LOW,
                cost="none",
                time="immediate",
                notes=["Consent is recorded and can be withdrawn at any time."],
            )
        )
        dilemma.add(
            Option(
                label="B",
                title="Skip it and stay in privacy mode",
                kind="conservative",
                expected_result="Nothing is captured; Jarvis continues with text only.",
                risk=RiskLevel.LOW,
                cost="reduced capability",
                time="immediate",
            )
        )
        return dilemma.cancel_option()

    # --- file helpers --------------------------------------------------------
    def safe_path(self, path: str, root: str) -> tuple[bool, str]:
        return self.host.file_operations_allowed(path, root)


class VisionAgent(Agent):
    name = "vision"
    description = "Interprets authorized camera or image input, within strict inference limits."
    capabilities = ("vision", "ocr", "scene", "camera")
    requires = ("numpy",)  # only needed for real frame processing

    def __init__(self, runtime: Any = None, *, host: HostAdapter | None = None) -> None:
        super().__init__(runtime)
        self.host = host or detect_host()

    def run(self, request: AgentRequest) -> AgentResult:
        question = (request.text or request.param("question") or "").strip()
        result = AgentResult(agent=self.name, summary=f"Vision request: {question or 'status'}")

        refusal = self._refusal(question)
        if refusal:
            result.ok = False
            result.summary = refusal
            result.add_claim(Claim.certain(refusal))
            return result

        available, reason = self.available()
        if self.host.camera_active or request.param("image_path"):
            result.add_claim(
                Claim(
                    statement="Frame analysis requires a vision model provider, which is not configured.",
                    confidence=Confidence.UNKNOWN,
                    caveats=["no image has been interpreted; nothing is being inferred"],
                )
            )
            result.ok = False
            result.summary = "No vision provider configured - Jarvis will not guess what it cannot see."
            result.follow_ups.append("Configure a vision-capable model provider to enable this.")
            return result

        result.ok = False
        result.summary = f"Camera is not active: {reason}"
        result.add_claim(Claim.certain(f"Camera state: {'on' if self.host.camera_active else 'off'}."))
        result.follow_ups.extend(
            [
                "Say 'camera on' to enable it - the OS indicator will show.",
                "Say 'privacy mode' to disable all capture.",
            ]
        )
        return result

    def _refusal(self, question: str) -> str:
        """Section 17: refuse sensitive inferences about people, up front."""
        low = question.lower()
        triggers = {
            "health": ("sick", "ill", "disease", "diagnos", "pregnant", "disab", "mental health"),
            "religion": ("religio", "faith", "muslim", "hindu", "christian", "sikh", "jewish"),
            "politics": ("political", "which party", "votes for"),
            "orientation": ("gay", "lesbian", "queer", "sexual orientation"),
            "criminal": ("criminal", "thief", "guilty", "will they commit"),
            "identity": ("who is this person", "identify this person", "recognise this face", "recognize this face"),
        }
        for category, words in triggers.items():
            if any(w in low for w in words):
                return (
                    f"Jarvis does not infer {category} attributes about people from images. "
                    "This is a fixed limit, not a capability gap. I can describe visible objects, "
                    "text, colours and layout instead."
                )
        return ""

    def privacy_state(self) -> dict[str, Any]:
        return {
            "camera": "ON" if self.host.camera_active else "OFF",
            "microphone": "ON" if self.host.mic_active else "OFF",
            "privacy_mode": self.host.privacy_mode.value,
            "consented_devices": sorted(self.host.consented_devices),
            "prohibited_inferences": list(PROHIBITED_INFERENCES),
        }

    def indicator(self) -> str:
        """The always-visible state line the UI shows (spec section 18)."""
        if self.host.privacy_mode is PrivacyMode.LOCKED:
            return "LOCKED"
        if self.host.privacy_mode is PrivacyMode.PRIVACY:
            return "PRIVACY MODE"
        if self.host.camera_active:
            return "CAMERA ACTIVE"
        return "CAMERA OFF"
