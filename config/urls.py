from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from apps.calling import list_views as calling_lists
from apps.calling import views as calling_views
from apps.core import dashboard_views
from apps.core.navigation import MODULES
from apps.crm import views as crm_views
from apps.operations import views as operations_views
from apps.tenancy import views as tenancy_views
from apps.workforce import views as workforce_views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="home"), name="root"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("api/telephony/", include("apps.telephony.urls")),
    path("admin/", admin.site.urls),
    path("api/", include("apps.core.urls")),
    path("home/", workforce_views.home, name="home"),
    path("employees/", workforce_views.employees, name="employees"),
    path("employees/hire/", workforce_views.hire, name="employee-hire"),
    path(
        "employees/<uuid:pk>/", workforce_views.employee_detail, name="employee-detail"
    ),
    path(
        "employees/<uuid:pk>/redraft/",
        workforce_views.employee_redraft,
        name="employee-redraft",
    ),
    path(
        "employees/<uuid:pk>/publish/",
        workforce_views.employee_publish,
        name="employee-publish",
    ),
    path(
        "employees/<uuid:pk>/discard/",
        workforce_views.employee_discard,
        name="employee-discard",
    ),
    path(
        "employees/<uuid:pk>/versions/<int:number>/restore/",
        workforce_views.employee_restore,
        name="employee-restore",
    ),
    path(
        "employees/<uuid:pk>/switch/",
        workforce_views.employee_switch,
        name="employee-switch",
    ),
    path(
        "employees/<uuid:pk>/voice-preview/",
        workforce_views.employee_voice_preview,
        name="employee-voice-preview",
    ),
    path(
        "employees/<uuid:pk>/delete/",
        workforce_views.employee_delete,
        name="employee-delete",
    ),
    path(
        "organisation/setup/",
        tenancy_views.organisation_setup,
        name="organisation-setup",
    ),
    path(
        "organisation/switch/",
        tenancy_views.organisation_switch,
        name="organisation-switch",
    ),
    path("campuses/", tenancy_views.campuses, name="campuses"),
    path("campuses/<uuid:pk>/", tenancy_views.campus_edit, name="campus-edit"),
    path("staff/add/", tenancy_views.member_add, name="member-add"),
    path("staff/<int:pk>/", tenancy_views.member_edit, name="member-edit"),
    path("leads/", crm_views.leads, name="leads"),
    path("leads/new/", crm_views.lead_new, name="lead-new"),
    path("leads/follow-ups/", crm_views.followups, name="followups"),
    path("leads/<uuid:pk>/", crm_views.lead_detail, name="lead-detail"),
    path("leads/<uuid:pk>/rehearse/", crm_views.lead_rehearse, name="lead-rehearse"),
    path("leads/<uuid:pk>/call/", crm_views.lead_call, name="lead-call"),
    path("contacts/", crm_views.contact_list, name="contacts"),
    path("contacts/import/", crm_views.contact_import, name="contact-import"),
    path("contacts/export/", crm_views.contact_export, name="contact-export"),
    path("contacts/<uuid:pk>/", crm_views.contact_detail, name="contact-detail"),
    path("conversations/", crm_views.conversations, name="conversations"),
    path(
        "conversations/export/",
        crm_views.conversations_export,
        name="conversations-export",
    ),
    path(
        "conversations/<uuid:pk>/",
        crm_views.conversation_detail,
        name="conversation-detail",
    ),
    path("make-calls/", calling_lists.make_calls, name="make-calls"),
    path("make-calls/<uuid:pk>/", calling_lists.call_list, name="call-list"),
    path(
        "make-calls/<uuid:pk>/entries/<int:entry_pk>/retry/",
        calling_lists.call_list_retry,
        name="call-list-retry",
    ),
    path(
        "conversations/<uuid:pk>/retry/",
        crm_views.conversation_retry,
        name="conversation-retry",
    ),
    path(
        "make-calls/<uuid:pk>/step/",
        calling_lists.call_list_step,
        name="call-list-step",
    ),
    path(
        "conversations/<uuid:pk>/recording/",
        crm_views.conversation_recording,
        name="conversation-recording",
    ),
    path("performance/", crm_views.performance, name="performance"),
    path("settings/", crm_views.privacy_settings, name="settings"),
    path("settings/access-log/", tenancy_views.audit_log, name="audit-log"),
    path(
        "settings/do-not-call/<int:pk>/remove/",
        crm_views.opt_out_remove,
        name="optout-remove",
    ),
    path("inbound/", calling_views.incoming, name="inbound"),
    path("numbers/", calling_views.numbers, name="numbers"),
    path("numbers/<uuid:pk>/", calling_views.number_edit, name="number-edit"),
    path("knowledge/", calling_views.knowledge_list, name="knowledge"),
    path("knowledge/<int:pk>/", calling_views.knowledge_edit, name="knowledge-edit"),
    path("notifications/", operations_views.notifications, name="notifications"),
    path("operations/", operations_views.operations, name="operations"),
    path("usage/", operations_views.usage, name="usage"),
    path("api/health/ready/", operations_views.readiness, name="readiness"),
    # Planned modules: each has a page saying what works today and what is to come.
    *(
        path(f"{slug}/", dashboard_views.module, {"slug": slug}, name=slug)
        for slug in MODULES
    ),
]
