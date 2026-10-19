from datetime import timedelta

# (code, label, due at this many weeks after birth)
# VERIFY against the current national immunisation schedule before use.
SCHEDULE = [
    ("BCG", "BCG", 0),
    ("OPV0", "OPV birth dose", 0),
    ("HEPB0", "Hepatitis B birth dose", 0),
    ("OPV1", "OPV 1", 6),
    ("PENTA1", "Pentavalent 1", 6),
    ("PCV1", "Pneumococcal 1", 6),
    ("ROTA1", "Rotavirus 1", 6),
    ("OPV2", "OPV 2", 10),
    ("PENTA2", "Pentavalent 2", 10),
    ("PCV2", "Pneumococcal 2", 10),
    ("ROTA2", "Rotavirus 2", 10),
    ("OPV3", "OPV 3", 14),
    ("PENTA3", "Pentavalent 3", 14),
    ("PCV3", "Pneumococcal 3", 14),
    ("IPV", "Polio (IPV)", 14),
    ("MEASLES", "Measles-rubella", 36),
]
SCHEDULE_CODES = {code for code, _, _ in SCHEDULE}
LABELS = {code: label for code, label, _ in SCHEDULE}
GRACE_WEEKS = 2


def schedule_status(child, today):
    """
    One row per scheduled vaccine with GIVEN, DUE, OVERDUE, or UPCOMING.
    Returns None if the child has no date of birth, because the schedule can't be worked out.
    """
    if child.date_of_birth is None:
        return None
    given = {v.vaccine: v for v in child.vaccinations.all()}
    age_weeks = (today - child.date_of_birth).days // 7
    rows = []
    for code, label, due_weeks in SCHEDULE:
        if code in given:
            status = "GIVEN"
        elif age_weeks >= due_weeks + GRACE_WEEKS:
            status = "OVERDUE"
        elif age_weeks >= due_weeks:
            status = "DUE"
        else:
            status = "UPCOMING"
        rows.append({
            "code": code,
            "label": label,
            "due_date": child.date_of_birth + timedelta(weeks=due_weeks),
            "status": status,
            "given": given.get(code),
        })
    return rows
