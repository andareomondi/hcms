import os

import django

# Setup Django environment (update 'hospital_cms.settings' if your project settings module name differs)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hospital_system.settings")
django.setup()

from django.utils import timezone

from triage_queue.models import Encounter, QueueEntry


def close_all_open_encounters():
    now = timezone.now()
    
    # Query all open encounters
    open_encounters = Encounter.objects.filter(status="OPEN")
    count = open_encounters.count()

    if count == 0:
        print("ℹ️ No open encounters found in the database.")
        return

    print(f"Found {count} open encounter(s). Closing...")

    # Bulk update encounters to CLOSED
    open_encounters.update(
        status="CLOSED",
        closed_at=now
    )

    # Sync and close associated active queue entries
    updated_queues = QueueEntry.objects.filter(
        encounter__in=open_encounters
    ).exclude(status="DISCHARGED").update(status="DISCHARGED")

    print(f"✅ Successfully closed {count} encounter(s) and marked {updated_queues} queue entries as DISCHARGED.")


if __name__ == "__main__":
    close_all_open_encounters()
