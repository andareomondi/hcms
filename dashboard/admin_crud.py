from functools import wraps

from django import forms
from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import ProtectedError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .audit_actions import AuditAction
from .utils import log_action
from .views import is_system_admin

# key: "app_label.ModelName"
# editable=False -> view only (no add, no change)
# deletable=False -> no delete
MANAGED = {
    "core_patients.Department": {},
    "core_patients.StaffProfile": {},
    "core_patients.Patient": {},
    "core_patients.PatientRelationship": {},
    "core_patients.AllergenClass": {},
    "core_patients.Allergen": {},
    "core_patients.PatientAllergy": {},
    "core_patients.AllergyObservation": {"editable": False, "deletable": False},
    "core_patients.StaffRegistrationRequest": {"editable": False, "deletable": False},
    "core_patients.ConsentRecord": {"editable": False, "deletable": False},
    "triage_queue.Encounter": {},
    "triage_queue.TriageVitals": {"editable": False, "deletable": False},
    "triage_queue.QueueEntry": {},
    "doctors_portal.ClinicalNote": {"editable": False, "deletable": False},
    "doctors_portal.ClinicalNoteAddendum": {"editable": False, "deletable": False},
    "pharmacy.Medication": {},
    "pharmacy.StockItem": {},
    "pharmacy.StockMovement": {"editable": False, "deletable": False},
    "pharmacy.MedicationOrder": {},
    "pharmacy.NursingAdministration": {"editable": False, "deletable": False},
    "pharmacy.Dispensation": {"editable": False, "deletable": False},
    "maternity.Delivery": {"editable": False, "deletable": False},
    "maternity.Vaccination": {"editable": False, "deletable": False},
    "radiology.ScanOrder": {},
    "radiology.ImagingStudy": {"editable": False, "deletable": False},
    "wards.Ward": {},
    "wards.Bed": {"editable": False, "deletable": False},
    "wards.Admission": {"editable": False, "deletable": False},
    "wards.BedStatusLog": {"editable": False, "deletable": False},
    "billing.BillingAccount": {},
    "billing.Charge": {"editable": False, "deletable": False},
    "billing.Payment": {"editable": False, "deletable": False},
    "duty.Room": {},
    "duty.DutySession": {"editable": False, "deletable": False},
    "notifications.Notification": {"editable": False, "deletable": True},
    "dashboard.AuditLog": {"editable": False, "deletable": False},
}

_LOOKUP = {k.lower(): k for k in MANAGED}


def superuser_required(view):
    decorated = login_required(
        user_passes_test(is_system_admin, login_url="/accounts/login/")(view)
    )
    return wraps(view)(decorated)


def _resolve(app_label, model_name):
    key = _LOOKUP.get(f"{app_label}.{model_name}".lower())
    if key is None:
        raise Http404("Model is not managed here.")
    model = apps.get_model(app_label, model_name)
    return model, MANAGED[key]


def _get_obj(model, pk):
    try:
        return model.objects.get(pk=pk)
    except (model.DoesNotExist, ValueError, ValidationError):
        raise Http404("Record not found.")


class _StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                "class", "w-full border border-slate-300 rounded-xl px-3 py-2 text-sm bg-white"
            )


def _form_for(model):
    fields = [f.name for f in model._meta.concrete_fields if f.editable and not f.primary_key]
    fields += [f.name for f in model._meta.many_to_many]
    return forms.modelform_factory(model, form=_StyledForm, fields=fields)

def _list_url(app_label, model_name):
    from django.urls import reverse
    return reverse("dashboard:crud_list", args=[app_label, model_name])


@superuser_required
def crud_index_view(request):
    rows = []
    for key, opts in MANAGED.items():
        app_label, name = key.split(".")
        model = apps.get_model(app_label, name)
        rows.append({
            "app": app_label,
            "name": name,
            "label": model._meta.verbose_name_plural.title(),
            "count": model.objects.count(),
            "editable": opts.get("editable", True),
            "deletable": opts.get("deletable", True),
        })
    return render(request, "dashboard/crud/index.html", {"rows": rows})


@superuser_required
def crud_list_view(request, app_label, model_name):
    model, opts = _resolve(app_label, model_name)
    ordering = model._meta.ordering or ["pk"]
    qs = model.objects.all().order_by(*ordering)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    columns = [f for f in model._meta.concrete_fields][:5]
    rows = [(obj, [getattr(obj, f.name) for f in columns]) for obj in page.object_list]
    return render(request, "dashboard/crud/list.html", {
        "app_label": app_label,
        "model_name": model_name,
        "label": model._meta.verbose_name_plural.title(),
        "page": page,
        "columns": [f.verbose_name for f in columns],
        "rows": rows,
        "editable": opts.get("editable", True),
        "deletable": opts.get("deletable", True),
    })


@superuser_required
def crud_create_view(request, app_label, model_name):
    model, opts = _resolve(app_label, model_name)
    if not opts.get("editable", True):
        messages.error(request, "This record type is append-only and cannot be created here.")
        return redirect(_list_url(app_label, model_name))

    form = _form_for(model)(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save()
            log_action(request, AuditAction.CREATE,
                       f"Created {model._meta.verbose_name} '{obj}' via data manager.")
        messages.success(request, f"{model._meta.verbose_name.title()} created.")
        return redirect(_list_url(app_label, model_name))

    return render(request, "dashboard/crud/form.html", {
        "form": form, "app_label": app_label, "model_name": model_name,
        "mode": "Create", "label": model._meta.verbose_name.title(),
    })


@superuser_required
def crud_edit_view(request, app_label, model_name, pk):
    model, opts = _resolve(app_label, model_name)
    if not opts.get("editable", True):
        messages.error(request, "This record is append-only and cannot be changed here.")
        return redirect(_list_url(app_label, model_name))

    obj = _get_obj(model, pk)
    form = _form_for(model)(request.POST or None, request.FILES or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        changed = ", ".join(form.changed_data) or "no fields"
        with transaction.atomic():
            obj = form.save()
            log_action(request, AuditAction.CREATE if False else AuditAction.PROFILE_UPDATED,
                       f"Updated {model._meta.verbose_name} '{obj}' ({changed}) via data manager.")
        messages.success(request, f"{model._meta.verbose_name.title()} saved.")
        return redirect(_list_url(app_label, model_name))

    return render(request, "dashboard/crud/form.html", {
        "form": form, "app_label": app_label, "model_name": model_name,
        "mode": "Edit", "obj": obj, "label": model._meta.verbose_name.title(),
    })


@superuser_required
@require_POST
def crud_delete_view(request, app_label, model_name, pk):
    model, opts = _resolve(app_label, model_name)
    if not opts.get("deletable", True):
        messages.error(request, "This record cannot be deleted.")
        return redirect(_list_url(app_label, model_name))

    obj = _get_obj(model, pk)
    label = str(obj)
    try:
        with transaction.atomic():
            obj.delete()
    except ProtectedError:
        messages.error(request, "Cannot delete: other records depend on this one. "
                                "Remove or reassign those first.")
        return redirect(_list_url(app_label, model_name))

    log_action(request, AuditAction.DELETE,
               f"Deleted {model._meta.verbose_name} '{label}' via data manager.")
    messages.success(request, "Record deleted.")
    return redirect(_list_url(app_label, model_name))
