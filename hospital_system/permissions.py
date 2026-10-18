"""
Single source of truth for who can do what.
Views use @requires(Cap.X). Templates use {% if "X" in caps %}.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


class Cap:
    PATIENT_SEARCH = "PATIENT_SEARCH"        # find patients in the registry
    PATIENT_REGISTER = "PATIENT_REGISTER"    # register and edit patient demographics
    PATIENT_FILE = "PATIENT_FILE"            # open a patient file (sections gated below)
    VIEW_CLINICAL = "VIEW_CLINICAL"          # triage data, vitals, clinical notes
    VIEW_PHARMACY = "VIEW_PHARMACY"          # medication orders, administrations, dispensing
    VIEW_RADIOLOGY = "VIEW_RADIOLOGY"        # scan orders, images, reports
    VIEW_FINANCE = "VIEW_FINANCE"            # charges, payments, receipts
    OPEN_ENCOUNTER = "OPEN_ENCOUNTER"        # open a visit
    TRIAGE_BOARD = "TRIAGE_BOARD"            # see and manage the waiting board
    CLINICAL_ACTIONS = "CLINICAL_ACTIONS"    # consult, write notes, prescribe, request scans, admit
    RADIOLOGY_REPORT = "RADIOLOGY_REPORT"    # upload images and write reports
    PHARMACY_MANAGE = "PHARMACY_MANAGE"      # formulary and stock
    DISPENSE = "DISPENSE"                    # dispense oral medicine
    NURSE_ADMINISTER = "NURSE_ADMINISTER"    # give injections and IV medicines
    WARD_VIEW = "WARD_VIEW"                  # see the bed board
    WARD_ACTIONS = "WARD_ACTIONS"            # assign, discharge, change bed status
    POST_CHARGES = "POST_CHARGES"            # post provisional charges
    TAKE_PAYMENT = "TAKE_PAYMENT"            # record a payment
    DUTY_SELF = "DUTY_SELF"                  # clock in and out
    ADMIN_CONFIG = "ADMIN_CONFIG"            # data manager, staff approvals, audit


ADMIN_VIEW_CAPS = {
    Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_CLINICAL, Cap.VIEW_PHARMACY,
    Cap.VIEW_RADIOLOGY, Cap.VIEW_FINANCE, Cap.WARD_VIEW, Cap.TRIAGE_BOARD, Cap.ADMIN_CONFIG,
    Cap.TAKE_PAYMENT,  
}

ROLE_CAPS = {
    "RECEPTION": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_REGISTER, Cap.PATIENT_FILE, Cap.OPEN_ENCOUNTER,
        Cap.TRIAGE_BOARD, Cap.WARD_VIEW, Cap.DUTY_SELF,
    },
    "TRIAGE_NURSE": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_CLINICAL, Cap.OPEN_ENCOUNTER,
        Cap.TRIAGE_BOARD, Cap.DUTY_SELF,
    },
    "DOCTOR": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_CLINICAL, Cap.VIEW_PHARMACY,
        Cap.VIEW_RADIOLOGY, Cap.CLINICAL_ACTIONS, Cap.WARD_VIEW, Cap.WARD_ACTIONS, Cap.DUTY_SELF,
    },
    "NURSE": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_CLINICAL, Cap.VIEW_PHARMACY,
        Cap.NURSE_ADMINISTER, Cap.WARD_VIEW, Cap.WARD_ACTIONS, Cap.DUTY_SELF,
    },
    "PHARMACIST": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_PHARMACY, Cap.PHARMACY_MANAGE,
        Cap.DISPENSE, Cap.DUTY_SELF,
    },
    "RADIOLOGIST": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_RADIOLOGY, Cap.RADIOLOGY_REPORT,
        Cap.DUTY_SELF,
    },
    "BILLING": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_FINANCE, Cap.POST_CHARGES,
        Cap.TAKE_PAYMENT, Cap.DUTY_SELF,
    },
}


def caps_for(user):
    """Set of capabilities for a user. Admin has view-only reach plus configuration."""
    if not user.is_authenticated:
        return set()
    profile = getattr(user, "staff_profile", None)
    if user.is_superuser or (profile is not None and profile.role == "ADMIN"):
        return set(ADMIN_VIEW_CAPS)
    if profile is None:
        return set()
    return set(ROLE_CAPS.get(profile.role, set()))


def has_any(user, *capabilities):
    have = caps_for(user)
    return any(c in have for c in capabilities)


def requires(*capabilities):
    """Use on views. Logged-in users without the capability get a 403. Anonymous users go to login."""
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not has_any(request.user, *capabilities):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapped
    return decorator
