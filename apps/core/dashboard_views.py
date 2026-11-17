"""Dashboard pages that belong to no single app."""

from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from apps.tenancy.access import scoped

from .navigation import MODULES


@scoped()
def module(request, slug):
    """An honest page for a module that is planned but not built yet."""
    info = MODULES.get(slug)
    if info is None:
        raise Http404
    today = [(reverse(name), text) for name, text in info["today"]]
    return render(
        request, "dashboard/module.html", {"module": info, "today_links": today}
    )
