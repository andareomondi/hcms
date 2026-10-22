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
    BED_MANAGE = "BED_MANAGE"                # change bed status by hand: cleaning, isolation, out of service
    ALLERGY_MANAGE = "ALLERGY_MANAGE"        # record, update, dispute, resolve allergies
    MATERNITY_RECORD = "MATERNITY_RECORD"    # record births and vaccinations
    CONSENT_RECORD = "CONSENT_RECORD"        # record and withdraw consent
    PATIENT_MERGE = "PATIENT_MERGE"          # merge duplicate patient records


# Collect every single capability defined in the Cap class
ALL_CAPS = {
    getattr(Cap, attr) for attr in dir(Cap) 
    if not attr.startswith("_") and isinstance(getattr(Cap, attr), str)
}

# Regular ADMIN role can do everything EXCEPT direct clinical/operational tasks and clocking
ADMIN_CAPS = ALL_CAPS - {
    Cap.DUTY_SELF, 
    Cap.CLINICAL_ACTIONS, 
    Cap.NURSE_ADMINISTER, 
    Cap.DISPENSE,
    Cap.RADIOLOGY_REPORT,
}

ROLE_CAPS = {
    "RECEPTION": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_REGISTER, Cap.PATIENT_FILE, Cap.OPEN_ENCOUNTER,
        Cap.TRIAGE_BOARD, Cap.WARD_VIEW, Cap.DUTY_SELF, Cap.BED_MANAGE, Cap.CONSENT_RECORD, Cap.PATIENT_MERGE,
    },
    "TRIAGE_NURSE": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_CLINICAL, Cap.OPEN_ENCOUNTER,
        Cap.TRIAGE_BOARD, Cap.DUTY_SELF, Cap.ALLERGY_MANAGE, Cap.CONSENT_RECORD, 
    },
    "DOCTOR": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_CLINICAL, Cap.VIEW_PHARMACY,
        Cap.VIEW_RADIOLOGY, Cap.CLINICAL_ACTIONS, Cap.WARD_VIEW, Cap.WARD_ACTIONS, Cap.DUTY_SELF, Cap.BED_MANAGE, Cap.ALLERGY_MANAGE, Cap.MATERNITY_RECORD, Cap.CONSENT_RECORD, Cap.RADIOLOGY_REPORT,
    },
    "NURSE": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_CLINICAL, Cap.VIEW_PHARMACY,
        Cap.NURSE_ADMINISTER, Cap.WARD_VIEW, Cap.WARD_ACTIONS, Cap.DUTY_SELF, Cap.BED_MANAGE, Cap.ALLERGY_MANAGE, Cap.MATERNITY_RECORD, Cap.CONSENT_RECORD, 
    },
    "PHARMACIST": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_PHARMACY, Cap.PHARMACY_MANAGE,
        Cap.DISPENSE, Cap.DUTY_SELF, Cap.ALLERGY_MANAGE,
    },
    "RADIOLOGIST": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_RADIOLOGY, Cap.RADIOLOGY_REPORT,
        Cap.DUTY_SELF, Cap.ALLERGY_MANAGE,
    },
    "BILLING": {
        Cap.PATIENT_SEARCH, Cap.PATIENT_FILE, Cap.VIEW_FINANCE, Cap.POST_CHARGES,
        Cap.TAKE_PAYMENT, Cap.DUTY_SELF,
    },
}


def caps_for(user):
    """
    Set of capabilities for a user.
    - Superusers have complete access with NO restrictions whatsoever (ALL_CAPS).
    - Regular ADMIN roles have configuration/view access minus direct clinical execution & clocking.
    """
    if not user.is_authenticated:
        return set()
    
    # Superuser gets every capability in the system
    if user.is_superuser:
        return set(ALL_CAPS)
        
    profile = getattr(user, "staff_profile", None)
    if profile is not None and profile.role == "ADMIN":
        return set(ADMIN_CAPS)
        
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
