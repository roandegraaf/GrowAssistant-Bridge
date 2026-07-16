"""Automation rule-set handling on the bridge (Phase 4).

The app is the editor + viewer; the bridge owns execution. The manager
*receives* the app's retained automation rule set, *validates* it (structure +
entity existence against the device registry), reports the result on the
retained ``…/automations/status`` topic, **and drives the evaluator** — it hands
the enabled rules to an ``AutomationEngine`` (slice 005) that actually runs them
(triggers/conditions/actions). The full HA-subset vocabulary is recognised here
so a rule that validates is a rule the engine runs.

Why validate on registry change, not only on receipt
-----------------------------------------------------
The rule set arrives on a *retained* topic, so the broker delivers it the
instant the bridge subscribes. Depending on boot ordering that can be before an
integration has registered its devices — a receipt-only check would then fail
every entity reference as "unknown" until the next push. Re-validating whenever
the registry changes (the same hook the manifest re-push uses) makes the status
self-correct. The engine itself resolves entities lazily (a rule for a device
that appears later just starts working), so it needs no rebuild on registry
change — only the *status* re-validates.

Hash round-trip + versioning
----------------------------
The app records the SHA-256 of the exact bytes it published; the bridge echoes
``validatedHash`` = SHA-256 of the bytes it received. Because it is the same
retained message, the bytes are identical and the hashes match without any
cross-language canonical-form parity (unlike the manifest hash). The app uses
the match (plus ``ok``) to distinguish "saved" from "confirmed by the bridge".

The payload also carries a monotonic ``version``. The manager persists the last
version it applied to the engine and rebuilds the engine only for a
*strictly-newer* version — so the retained message redelivered on every
reconnect does not reset edge-trigger baselines. This is the clear-while-offline
fix: a clear now arrives as ``{automations:[], version:N}`` (the retained
message is never deleted), so a bridge offline at clear time empties its engine
when it reconnects rather than running deleted rules from cache.
"""

import hashlib
import json
import logging
from collections.abc import Awaitable
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.config_store import config_store
from app.registry import registry

from .engine import AutomationEngine

logger = logging.getLogger(__name__)

# ConfigStore key under which the last-received rule set is cached across restarts.
CONFIG_KEY = "automations"

# Recognised vocabulary — mirrors the app's Zod schema. Structural validation
# only; the evaluator slice gives these runtime behaviour.
TRIGGER_TYPES = {"state", "numeric_state", "time", "time_pattern", "event"}
CONDITION_TYPES = {"state", "numeric_state", "time", "and", "or", "not"}
ACTION_TYPES = {"call", "delay", "wait_for_state", "set_variable", "fire_event", "notification"}

# Node types that reference an entity (validated against the registry).
_ENTITY_TRIGGERS = {"state", "numeric_state"}
_ENTITY_CONDITIONS = {"state", "numeric_state"}
_ENTITY_ACTIONS = {"call", "wait_for_state"}
_LOGICAL_CONDITIONS = {"and", "or", "not"}

StatusPublisher = Callable[[dict[str, Any]], Awaitable[Any]]


class AutomationManager:
    """Receives, caches and validates the per-bridge automation rule set."""

    def __init__(self) -> None:
        self._raw: Optional[str] = None  # exact payload string last received ("" = cleared)
        self._automations: list[dict[str, Any]] = []
        self._publish_status: Optional[StatusPublisher] = None
        self._engine: Optional[AutomationEngine] = None
        # Last rule-set version applied to the engine (None = none applied yet).
        self._applied_version: Optional[int] = None

        # Restore the last-applied rule set so a restart re-validates + re-reports
        # — and runs the cached rules locally — without needing the app to re-push.
        cached = config_store.get_config(CONFIG_KEY)
        if cached is not None and isinstance(cached.get("payload"), str):
            self._raw = cached["payload"]
            self._automations = self._parse_list(self._raw)
            v = cached.get("version")
            self._applied_version = v if isinstance(v, int) else None
        logger.info(
            "AutomationManager initialized (%d cached automation(s), version=%s)",
            len(self._automations),
            self._applied_version,
        )

    def set_status_publisher(self, fn: StatusPublisher) -> None:
        """Register the coroutine that publishes a status dict (transport-provided)."""
        self._publish_status = fn

    # ─── Engine wiring ──────────────────────────────────────────────

    def set_engine(self, engine: AutomationEngine) -> None:
        """Attach the evaluator the manager drives."""
        self._engine = engine

    def start_engine(self) -> None:
        """Load the cached enabled rules into the engine and start it.

        Called once at startup (after wiring). The cached rules run locally even
        before the bridge reconnects to the broker — pillar P5 (offline-tolerant
        automations)."""
        if self._engine is None:
            return
        self._engine.apply_rules(self._enabled_rules())
        self._engine.start()

    async def stop_engine(self) -> None:
        """Stop the evaluator (cancels in-flight runs + the scheduler)."""
        if self._engine is not None:
            await self._engine.stop()

    def _enabled_rules(self) -> list[dict[str, Any]]:
        """The subset of the cached rule set that is enabled (runs on the engine)."""
        return [
            r for r in self._automations if isinstance(r, dict) and r.get("enabled", True) is True
        ]

    def _persist(self, text: str) -> None:
        """Cache the raw payload + applied version for restart recovery."""
        config_store.save_config(
            CONFIG_KEY,
            {"payload": text, "version": self._applied_version},
            len(self._automations),
        )

    # ─── Inbound ────────────────────────────────────────────────────

    async def apply_payload(self, payload: bytes) -> dict[str, Any]:
        """Handle an inbound retained ``…/automations`` message.

        An empty payload clears the rule set (the app publishes empty bytes to
        delete the retained message when the last automation is removed). Always
        publishes a status afterwards.
        """
        validated_hash = hashlib.sha256(payload or b"").hexdigest()
        text = payload.decode("utf-8") if payload else ""

        if not text.strip():
            # Empty *bytes* — a deleted retained message (legacy/edge; the app now
            # clears via a non-empty `{automations:[], version:N}`). Stop running
            # any rules but KEEP the applied version, so this never *downgrades*
            # the reconciliation guard and let a future versioned set win.
            self._raw = ""
            self._automations = []
            self._persist("")
            if self._engine is not None:
                self._engine.apply_rules([])
            return await self._emit_status(ok=True, errors=[], validated_hash=validated_hash)

        try:
            data = json.loads(text)
            automations = data.get("automations") if isinstance(data, dict) else None
            if not isinstance(automations, list):
                raise ValueError("payload must be an object with an 'automations' array")
        except (ValueError, TypeError) as e:
            self._raw = text
            self._automations = []
            return await self._emit_status(
                ok=False,
                errors=[{"automationId": None, "message": f"invalid payload: {e}"}],
                validated_hash=validated_hash,
            )

        raw_version = data.get("version")
        incoming_version = raw_version if isinstance(raw_version, int) else None
        errors = self.validate(automations)

        # Reconcile against the version guard: apply (and rebuild the engine) only
        # for a strictly-newer version. A versionless payload (legacy) always
        # applies; the retained set redelivered on reconnect (same version) is a
        # no-op for the engine so edge-trigger baselines are not reset.
        if (
            incoming_version is None
            or self._applied_version is None
            or incoming_version > self._applied_version
        ):
            self._raw = text
            self._automations = automations
            if incoming_version is not None:
                self._applied_version = incoming_version
            self._persist(text)
            if self._engine is not None:
                enabled = self._enabled_rules()
                self._engine.apply_rules(enabled)
                # Seed a real lifecycle event so rules can react to a (re)deploy.
                self._engine.emit_event("rule_set_applied", {"count": len(enabled)})
        else:
            logger.info(
                "Ignoring stale automations payload (version=%s, applied=%s)",
                incoming_version,
                self._applied_version,
            )

        return await self._emit_status(ok=not errors, errors=errors, validated_hash=validated_hash)

    async def revalidate(self) -> Optional[dict[str, Any]]:
        """Re-validate the cached rule set and republish status.

        Called on registry change. No-op (returns None) if nothing has ever been
        received.
        """
        if self._raw is None:
            return None
        validated_hash = hashlib.sha256(self._raw.encode("utf-8")).hexdigest()
        if not self._raw.strip():
            return await self._emit_status(ok=True, errors=[], validated_hash=validated_hash)
        errors = self.validate(self._automations)
        return await self._emit_status(ok=not errors, errors=errors, validated_hash=validated_hash)

    # ─── Validation ─────────────────────────────────────────────────

    def validate(self, automations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return a list of ``{automationId, message}`` errors (empty = valid).

        Structural checks (known types, at least one trigger + one action) plus
        entity-existence against the registry. No service-applicability or
        execution-time checks — those belong to the evaluator slice.
        """
        errors: list[dict[str, Any]] = []
        for i, rule in enumerate(automations):
            if not isinstance(rule, dict):
                errors.append(
                    {"automationId": None, "message": f"automations[{i}] must be an object"}
                )
                continue
            rid = rule.get("id")
            triggers = rule.get("triggers")
            conditions = rule.get("conditions") or []
            actions = rule.get("actions")

            if not isinstance(triggers, list) or not triggers:
                errors.append({"automationId": rid, "message": "at least one trigger is required"})
                triggers = triggers if isinstance(triggers, list) else []
            if not isinstance(actions, list) or not actions:
                errors.append({"automationId": rid, "message": "at least one action is required"})
                actions = actions if isinstance(actions, list) else []

            for t in triggers:
                self._check_node(t, TRIGGER_TYPES, _ENTITY_TRIGGERS, "trigger", rid, errors)
            for c in conditions if isinstance(conditions, list) else []:
                self._check_condition(c, rid, errors)
            for a in actions:
                self._check_node(a, ACTION_TYPES, _ENTITY_ACTIONS, "action", rid, errors)
        return errors

    def _check_node(
        self,
        node: Any,
        valid_types: set[str],
        entity_types: set[str],
        label: str,
        rid: Any,
        errors: list[dict[str, Any]],
    ) -> None:
        if not isinstance(node, dict):
            errors.append({"automationId": rid, "message": f"{label} must be an object"})
            return
        ntype = node.get("type")
        if ntype not in valid_types:
            errors.append({"automationId": rid, "message": f"unknown {label} type '{ntype}'"})
            return
        if ntype in entity_types:
            self._check_entity(node.get("entity"), label, ntype, rid, errors)
        elif ntype == "notification":
            # Only ever reached for an action node ("notification" ∈ ACTION_TYPES only).
            self._check_notification(node, rid, errors)

    def _check_condition(self, node: Any, rid: Any, errors: list[dict[str, Any]]) -> None:
        if not isinstance(node, dict):
            errors.append({"automationId": rid, "message": "condition must be an object"})
            return
        ctype = node.get("type")
        if ctype not in CONDITION_TYPES:
            errors.append({"automationId": rid, "message": f"unknown condition type '{ctype}'"})
            return
        if ctype in _LOGICAL_CONDITIONS:
            sub = node.get("conditions") or []
            for child in sub if isinstance(sub, list) else []:
                self._check_condition(child, rid, errors)
        elif ctype in _ENTITY_CONDITIONS:
            self._check_entity(node.get("entity"), "condition", ctype, rid, errors)

    @staticmethod
    def _check_entity(
        entity: Any, label: str, ntype: str, rid: Any, errors: list[dict[str, Any]]
    ) -> None:
        if not entity or not isinstance(entity, str):
            errors.append({"automationId": rid, "message": f"{label} '{ntype}' requires an entity"})
        elif registry.get_device(entity) is None:
            errors.append({"automationId": rid, "message": f"unknown entity '{entity}'"})

    @staticmethod
    def _check_notification(node: dict[str, Any], rid: Any, errors: list[dict[str, Any]]) -> None:
        """A ``notification`` action needs a non-empty string ``title`` and
        ``message`` (the engine renders them as ``{{ … }}`` templates at run time)."""
        for field in ("title", "message"):
            value = node.get(field)
            if not value or not isinstance(value, str):
                errors.append(
                    {"automationId": rid, "message": f"action 'notification' requires a {field}"}
                )

    # ─── Status output ──────────────────────────────────────────────

    async def _emit_status(
        self, ok: bool, errors: list[dict[str, Any]], validated_hash: str
    ) -> dict[str, Any]:
        status = {
            "ok": ok,
            "count": len(self._automations),
            "validatedHash": validated_hash,
            "validatedAt": datetime.now(timezone.utc).isoformat(),
            "errors": errors,
        }
        if self._publish_status is not None:
            try:
                await self._publish_status(status)
            except Exception:
                logger.exception("Failed to publish automations status")
        logger.info(
            "Automations validated: ok=%s, count=%d, %d error(s)",
            ok,
            status["count"],
            len(errors),
        )
        return status

    @staticmethod
    def _parse_list(text: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            return []
        automations = data.get("automations") if isinstance(data, dict) else None
        return automations if isinstance(automations, list) else []
