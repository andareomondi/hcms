"""
After-birth report (PDF). Generated from the signed (or pending) delivery episode.
Contains everything needed for the mother's and each baby's file in one document.
"""
from datetime import timedelta
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from billing import services as billing
from billing.models import Charge
from maternity.models import Delivery, Pregnancy
from maternity.schedule import SCHEDULE


def _table(rows, widths=None):
    t = Table(rows, colWidths=widths or [55 * mm, 115 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("BACKGROUND", (0, 0), (0, -1), colors.whitesmoke),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    return t


def _fmt(value, suffix=""):
    if value is None or value == "":
        return "Not recorded"
    return f"{value}{suffix}"


def build_after_birth_report(episode):
    styles = getSampleStyleSheet()
    h = styles["Heading2"]
    body = styles["BodyText"]
    story = []

    mother = episode.mother_encounter.patient
    pregnancy = Pregnancy.objects.filter(patient=mother).order_by("-opened_at").first()
    babies = list(episode.babies.select_related("baby").order_by("pk"))
    first = babies[0] if babies else None
    stay = episode.labour_stay

    story.append(Paragraph("After-Birth Report", styles["Title"]))
    status = "SIGNED OFF" if episode.status == "SIGNED" else "DRAFT: pending doctor sign-off"
    story.append(Paragraph(f"Status: {status}. Generated {timezone.localtime():%Y-%m-%d %H:%M}.", body))
    story.append(Spacer(1, 6 * mm))

    # 1. Mother
    story.append(Paragraph("1. Mother", h))
    story.append(_table([
        ["Name", f"{mother.first_name} {mother.last_name}".strip()],
        ["Patient ID", str(mother.pk)],
        ["Sex", mother.get_sex_display()],
        ["Last menstrual period", _fmt(pregnancy.lmp if pregnancy else None)],
        ["Estimated due date", _fmt(pregnancy.edd if pregnancy else None)],
        ["Allergies", "See patient record (allergy status)"],
        ["Complications", _fmt(episode.complications)],
    ]))
    story.append(Spacer(1, 4 * mm))

    # 2. Delivery
    story.append(Paragraph("2. Delivery", h))
    delivered = first.delivered_at if first else None
    story.append(_table([
        ["Date and time of birth", _fmt(timezone.localtime(delivered).strftime("%Y-%m-%d %H:%M") if delivered else None)],
        ["Type of delivery", _fmt(first.get_delivery_type_display() if first else None)],
        ["Number of babies", str(len(babies))],
        ["Delivered before arrival", "Yes" if episode.delivered_before_arrival else "No"],
        ["Arrived at labour unit", _fmt(timezone.localtime(stay.arrived_at).strftime("%Y-%m-%d %H:%M") if stay else None)],
        ["Labour bed", _fmt(stay.labour_bed.code if stay else None)],
        ["Recorded by", str(episode.recorded_by)],
        ["Complications", _fmt(episode.complications)],
    ]))
    story.append(Spacer(1, 4 * mm))

    # 3. Each baby, with vitals at birth
    story.append(Paragraph("3. Babies and vitals at birth", h))
    today = timezone.localdate()
    for i, d in enumerate(babies, start=1):
        baby = d.baby
        name = f"{baby.first_name} {baby.last_name}".strip() or "Temporary name"
        if baby.is_temporary_name:
            name += " (temporary name)"
        story.append(Paragraph(f"Baby {i}: {name}", body))
        story.append(_table([
            ["Patient ID", str(baby.pk)],
            ["Sex", d.get_baby_sex_display()],
            ["Date of birth", _fmt(baby.date_of_birth)],
            ["Birth weight", _fmt(d.birth_weight_g, " g")],
            ["Length", _fmt(d.birth_length_cm, " cm")],
            ["Head circumference", _fmt(d.head_circumference_cm, " cm")],
            ["Gestation", _fmt(d.gestation_weeks, " weeks")],
            ["APGAR at 1 minute", _fmt(d.apgar_1)],
            ["APGAR at 5 minutes", _fmt(d.apgar_5)],
            ["Heart rate", _fmt(d.heart_rate_bpm, " bpm")],
            ["Respiratory rate", _fmt(d.respiratory_rate, " breaths/min")],
            ["Temperature", _fmt(d.temperature_c, " °C")],
            ["Oxygen saturation", _fmt(d.spo2_percent, " %")],
            ["Blood pressure", (f"{d.bp_systolic}/{d.bp_diastolic} mmHg"
                                if d.bp_systolic and d.bp_diastolic else "Not recorded")],
            ["Notes", _fmt(d.notes)],
        ]))
        story.append(Spacer(1, 3 * mm))

        # 4. Immunisation schedule for this baby
        story.append(Paragraph(f"4. Immunisation schedule: Baby {i}", body))
        dob = baby.date_of_birth
        rows = [["Vaccine", "Due date", "Status"]]
        given = {v.vaccine: v for v in baby.vaccinations.all()}
        for code, label, weeks in SCHEDULE:
            due = dob + timedelta(weeks=weeks) if dob else None
            if code in given:
                status_text = f"Given {timezone.localtime(given[code].given_at):%Y-%m-%d}"
            elif due and due < today:
                status_text = "OVERDUE"
            else:
                status_text = "Due"
            rows.append([label, _fmt(due and due.isoformat()), status_text])
        story.append(_table(rows, widths=[70 * mm, 45 * mm, 55 * mm]))
        story.append(Spacer(1, 3 * mm))

    # 5. Parents and consent
    story.append(Paragraph("5. Parents and guarantor", h))
    parent_rows = [["Relationship", "Name", "Billing guarantor"]]
    for d in babies:
        for rel in d.baby.relationships_out.filter(is_active=True).select_related("related_patient"):
            parent_rows.append([rel.get_relationship_display() if hasattr(rel, "get_relationship_display")
                                else rel.relationship, str(rel.related_patient),
                                "Yes" if rel.is_billing_guarantor else "No"])
    story.append(_table(parent_rows, widths=[45 * mm, 85 * mm, 40 * mm]))
    story.append(Spacer(1, 4 * mm))

    # 6. Billing summary, per account
    story.append(Paragraph("6. Billing", h))
    bill_rows = [["Account", "Item", "Amount (KES)", "Status"]]
    accounts = [("Mother", episode.mother_encounter)]
    for d in babies:
        nursery = d.baby.encounters.filter(encounter_type="CHILD_HEALTH").first()
        if nursery:
            accounts.append((f"Baby {d.baby.first_name or 'temporary'}".strip(), nursery))
    for label, enc in accounts:
        tally = billing.encounter_tally(enc)
        for c in Charge.objects.filter(encounter=enc).exclude(status="VOIDED"):
            bill_rows.append([label, c.description, f"{c.amount:,.2f}", c.status])
        bill_rows.append([label, "Outstanding", f"{tally['outstanding']:,.2f}", ""])
    story.append(_table(bill_rows, widths=[35 * mm, 65 * mm, 35 * mm, 35 * mm]))
    story.append(Spacer(1, 4 * mm))

    # 7. Doctor sign-off
    story.append(Paragraph("7. Doctor sign-off", h))
    if episode.status == "SIGNED":
        story.append(_table([
            ["Signed off by", str(episode.signed_off_by)],
            ["Signed off at", timezone.localtime(episode.signed_off_at).strftime("%Y-%m-%d %H:%M")],
            ["Sign-off note", episode.signoff_note],
        ]))
    else:
        story.append(Paragraph("Not yet signed off. This report is a draft.", body))
    story.append(Spacer(1, 4 * mm))

    # 8. Audit
    story.append(Paragraph("8. Recorded by", h))
    story.append(_table([
        ["Delivery recorded by", str(episode.recorded_by)],
        ["Recorded at", timezone.localtime(episode.recorded_at).strftime("%Y-%m-%d %H:%M")],
    ]))

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm, title="After-Birth Report")
    doc.build(story)
    return buffer.getvalue()
