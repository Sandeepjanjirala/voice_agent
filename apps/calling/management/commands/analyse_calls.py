"""Summarise finished calls that have a transcript and no result yet.

    manage.py analyse_calls            preview: how many are waiting
    manage.py analyse_calls --apply    analyse them and carry out their actions

Meant for a scheduled task (every few minutes). Each call is analysed once;
the actions that follow run once, and are logged on the call.
"""

from django.core.management.base import BaseCommand

from apps.calling import analysis


class Command(BaseCommand):
    help = "Analyse finished calls that have a kept transcript."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--apply", action="store_true", help="Analyse them.")
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, apply: bool, limit: int, **options) -> None:
        waiting = list(analysis.pending()[: max(limit, 1)])
        if not apply:
            self.stdout.write(f"{len(waiting)} calls are waiting to be analysed.")
            self.stdout.write("Nothing was changed. Add --apply to analyse them.")
            return
        counts: dict[str, int] = {}
        for result in waiting:
            done = analysis.analyse(result.call)
            counts[done.status] = counts.get(done.status, 0) + 1
        summary = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
        self.stdout.write(f"Analysed {len(waiting)} calls: {summary or 'none'}.")
