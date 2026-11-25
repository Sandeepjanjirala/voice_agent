"""Apply each organisation's retention policy. Shows what would happen unless
--apply is given, so it is safe to run by hand and suits a scheduled task:

    manage.py retention            preview
    manage.py retention --apply    erase and delete what the policies say
"""

from django.core.management.base import BaseCommand

from apps.crm import retention
from apps.tenancy.models import Organisation


class Command(BaseCommand):
    help = "Preview or apply the data-retention policy of every organisation."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true", help="Carry it out.")

    def handle(self, *args, apply: bool, **options) -> None:
        for organisation in Organisation.objects.all():
            counts = (
                retention.apply(organisation)
                if apply
                else retention.preview(organisation)
            )
            verb = "removed" if apply else "would remove"
            self.stdout.write(
                f"{organisation.name}: {verb} {counts['contacts']} contacts, "
                f"{counts['calls']} call records, {counts['transcripts']} transcripts, "
                f"{counts['recordings']} recordings, "
                f"{counts['audit']} access-log entries"
            )
        if not apply:
            self.stdout.write("Nothing was changed. Add --apply to carry it out.")
