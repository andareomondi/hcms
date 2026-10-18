import os

import django

# Setup Django environment (update 'hospital_cms.settings' if your project settings module name differs)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hospital_system.settings")
django.setup()

from django.utils import timezone

from triage_queue.models import Encounter, QueueEntry


def discharge_active_queue_entries():
    now = timezone.now()
    
    # Target all queue entries that have not yet been discharged (Waiting, In Consultation, etc.)
    active_entries = QueueEntry.objects.exclude(status="DISCHARGED")
    count = active_entries.count()

    if count == 0:
        print("ℹ️ No active or waiting queue entries found in the database.")
        return

    print(f"Found {count} active/waiting queue entry/entries. Discharging...")

    # Grab associated open encounter IDs before updating queue status
    encounter_ids = active_entries.values_list('encounter_id', flat=True)

    # Update queue entries to DISCHARGED
    updated_queue_count = active_entries.update(status="DISCHARGED")

    # Optionally close their open clinical encounters as well
    open_encounters = Encounter.objects.filter(id__in=encounter_ids, status="OPEN")
    closed_encounter_count = open_encounters.update(
        status="CLOSED",
        closed_at=now
    )

    print(f"✅ Successfully discharged {updated_queue_count} queue entry/entries and closed {closed_encounter_count} associated encounter(s).")


if __name__ == "__main__":
    discharge_active_queue_entries()
