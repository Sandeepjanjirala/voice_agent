from django.core.management.base import BaseCommand, CommandError

from apps.calling.models import QueuedCall
from apps.calling.outbound import run_campaign_entry
from apps.telephony.calls import CallError


class Command(BaseCommand):
    help = (
        "Explicitly dispatch one due campaign entry under the current dialing policy."
    )

    def add_arguments(self, parser):
        parser.add_argument("entry_id", type=int)
        parser.add_argument("--confirm", action="store_true")

    def handle(self, entry_id, confirm=False, **options):
        if not confirm:
            raise CommandError(
                "This places a billed call. Pass --confirm to dispatch one entry."
            )
        try:
            record = run_campaign_entry(entry_id)
        except (CallError, QueuedCall.DoesNotExist) as error:
            raise CommandError(str(error)) from None
        self.stdout.write(f"Call dispatched: {record.pk}")
