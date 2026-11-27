"""Recover a call's actual destination without guessing from masked digits."""

from apps.crm.phone_display import decrypted_number

from .masking import mask_phone


def destination_number(record) -> str:
    """Use the encrypted call snapshot, or an unambiguous historical link.

    A rehearsal's lead is not the person who was dialled. Current allowlists
    are also not evidence of a historical destination, so neither is used.
    """
    contact = record.contact if record.contact_id else None
    if contact is not None and contact.is_erased:
        return ""
    if record.remote_encrypted:
        return decrypted_number(record.remote_encrypted)
    if record._state.adding or not record.organisation_id:
        return ""

    candidates = set()
    entries = record.list_entries.filter(
        call_list__organisation_id=record.organisation_id
    )
    for entry in entries:
        number = decrypted_number(entry.phone_encrypted)
        if number and mask_phone(number) == record.masked_number:
            candidates.add(number)
    if candidates:
        return candidates.pop() if len(candidates) == 1 else ""

    if record.is_rehearsal:
        return ""
    if (
        contact is None
        and record.lead_id
        and record.context_snapshot.get("rehearsal") is False
    ):
        lead = record.lead
        if lead.organisation_id == record.organisation_id:
            contact = lead.contact
    if (
        contact is not None
        and not contact.is_erased
        and contact.organisation_id == record.organisation_id
    ):
        number = decrypted_number(contact.phone_encrypted)
        if number and mask_phone(number) == record.masked_number:
            return number
    return ""
