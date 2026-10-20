from .models import Patient


def find_candidates(*, first_name="", last_name="", date_of_birth=None, phone="", id_number=""):
    """
    Likely matches for a new registration, strongest first. Merged records are ignored.
    Each candidate lists the reasons it matched, so reception can judge it.
    """
    base = Patient.objects.filter(merged_into__isnull=True)
    found = {}

    def add(queryset, reason, weight):
        for patient in queryset[:20]:
            entry = found.setdefault(patient.pk, {"patient": patient, "reasons": [], "score": 0})
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
                entry["score"] += weight

    first = first_name.strip()
    last = last_name.strip()
    phone = phone.strip()
    id_number = id_number.strip()

    if id_number:
        add(base.filter(id_number__iexact=id_number), "Same ID number", 100)
    if phone:
        add(base.filter(phone=phone), "Same phone number", 30)
    if last:
        same_last = base.filter(last_name__iexact=last)
        if first:
            add(same_last.filter(first_name__iexact=first), "Same first and last name", 50)
            add(same_last.filter(first_name__istartswith=first[:3]), "Similar first name", 15)
        if date_of_birth:
            add(same_last.filter(date_of_birth=date_of_birth), "Same surname and date of birth", 60)
            if first:
                add(same_last.filter(first_name__iexact=first, date_of_birth=date_of_birth),
                    "Same name and date of birth", 40)

    return sorted(found.values(), key=lambda c: c["score"], reverse=True)[:10]
