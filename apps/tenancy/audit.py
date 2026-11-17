"""The access log: one line per look at, or change to, personal data."""

from apps.telephony.masking import mask_phones_in

from .models import AuditEvent


def _safe(value):
    """Detail is free text from many places; a number must never land in it."""
    if isinstance(value, str):
        return mask_phones_in(value)[:300]
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [_safe(item) for item in value][:20]
    return mask_phones_in(str(value))[:300]


def record(scope, action: str, target=None, *, campus=None, **detail) -> AuditEvent:
    """One entry for something a signed-in person did."""
    return AuditEvent.objects.create(
        organisation=scope.organisation,
        campus=campus or getattr(target, "campus", None),
        actor=scope.user if getattr(scope.user, "is_authenticated", False) else None,
        actor_name=scope.user.get_username(),
        action=action,
        target_type=type(target).__name__.lower() if target is not None else "",
        target_id=str(getattr(target, "pk", "") or ""),
        detail={key: _safe(value) for key, value in detail.items()},
    )


def record_system(organisation, action: str, actor=None, **detail) -> AuditEvent:
    """An entry for a job that runs without a request, such as retention."""
    return AuditEvent.objects.create(
        organisation=organisation,
        actor=actor,
        actor_name=actor.get_username() if actor else "system",
        action=action,
        detail={key: _safe(value) for key, value in detail.items()},
    )
