"""
Sidebar structure. Each group is a purpose, not a department.
An item is shown only if the user has one of its capabilities (or is a superuser when marked).
"""
from django.urls import reverse

from .permissions import Cap

GROUPS = [
    {
        "label": "Home",
        "items": [
            {"label": "Overview", "url": "dashboard:overview", "icon": "📊"},
        ],
    },
    {
        "label": "Patients & front desk",
        "items": [
            {"label": "Patient registry", "url": "core_patients:search", "icon": "👥",
             "caps": [Cap.PATIENT_SEARCH]},
            {"label": "Register patient", "url": "core_patients:register", "icon": "➕",
             "caps": [Cap.PATIENT_REGISTER]},
            {"label": "Triage board", "url": "triage_queue:board", "icon": "⚡",
             "caps": [Cap.TRIAGE_BOARD]},
        ],
    },
    {
        "label": "Clinical",
        "items": [
            {"label": "My consultations", "url": "doctors_portal:dashboard", "icon": "🩺",
             "caps": [Cap.CLINICAL_ACTIONS]},
            {"label": "Nurse worklist", "url": "pharmacy:nurse_worklist", "icon": "💉",
             "caps": [Cap.NURSE_ADMINISTER]},
            {"label": "Pharmacy", "url": "pharmacy:dashboard", "icon": "💊",
             "caps": [Cap.VIEW_PHARMACY]},
            {"label": "Radiology", "url": "radiology:dashboard", "icon": "🩻",
             "caps": [Cap.VIEW_RADIOLOGY]},
        ],
    },
    {
        "label": "Wards & beds",
        "items": [
            {"label": "Bed board", "url": "wards:dashboard", "icon": "🛏️",
             "caps": [Cap.WARD_VIEW]},
            {"label": "Occupancy log", "url": "wards:occupancy", "icon": "🗓️",
             "caps": [Cap.WARD_VIEW]},
        ],
    },
    {
        "label": "Finance",
        "items": [
            {"label": "Bills & payments", "url": "billing:bills_ready", "icon": "💳",
             "caps": [Cap.VIEW_FINANCE]},
            {"label": "Charges ledger", "url": "billing:dashboard", "icon": "🧾",
             "caps": [Cap.VIEW_FINANCE]},
        ],
    },
    {
        "label": "My work",
        "items": [
            {"label": "Clock in / out", "url": "duty:clock_in", "icon": "🕒",
             "caps": [Cap.DUTY_SELF]},
            {"label": "Doctors on duty", "url": "duty:board", "icon": "🩺"},
            {"label": "Role guide", "url": "dashboard:role_guide", "icon": "📘"},
        ],
    },
    {
        "label": "Administration",
        "items": [
            {"label": "Admin panel", "url": "dashboard:admin_master", "icon": "🛡️",
             "superuser": True},
            {"label": "Data manager", "url": "dashboard:crud_index", "icon": "🗂️",
             "superuser": True},
            {"label": "Staff requests", "url": "core_patients:staff_requests", "icon": "📝",
             "superuser": True},
            {"label": "Audit log", "url": "dashboard:audit_logs", "icon": "📜",
             "caps": [Cap.ADMIN_CONFIG]},
        ],
    },
]


def build_nav(request, caps):
    path = request.path
    is_super = request.user.is_superuser
    groups = []

    for group in GROUPS:
        items = []
        for item in group["items"]:
            if item.get("superuser") and not is_super:
                continue
            if item.get("caps") and not any(c in caps for c in item["caps"]):
                continue
            url = reverse(item["url"])
            active = path == url or (url != "/" and path.startswith(url))
            items.append({**item, "href": url, "active": active})

        if items:
            groups.append({
                "label": group["label"],
                "items": items,
                "open": any(i["active"] for i in items),
            })
    return groups
