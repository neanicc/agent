from __future__ import annotations


class NotificationDeliveryError(RuntimeError):
    pass


class NotificationService:
    """Idempotent notification activity boundary keyed by the domain action ID."""

    def __init__(self) -> None:
        self.fail_first_attempt = False
        self._attempts: dict[str, int] = {}
        self._accepted: dict[str, None] = {}
        self._payloads: list[dict[str, object]] = []

    def deliver_action(self, *, action_id: str, idempotency_key: str) -> None:
        if not action_id or idempotency_key != f"notify-action:{action_id}":
            raise ValueError("notification idempotency key is invalid")
        if action_id in self._accepted:
            return
        attempts = self._attempts.get(action_id, 0) + 1
        self._attempts[action_id] = attempts
        if self.fail_first_attempt and attempts == 1:
            raise NotificationDeliveryError("injected transient delivery failure")
        self._accepted[action_id] = None
        self._payloads.append(
            {
                "aps": {
                    "alert": "LoopGuard needs your attention",
                    "sound": "default",
                },
                "type": "action",
                "action_id": action_id,
                "event_id": idempotency_key,
            }
        )

    @property
    def accepted_ids(self) -> list[str]:
        return list(self._accepted)

    @property
    def payloads(self) -> list[dict[str, object]]:
        return [dict(payload) for payload in self._payloads]
