import argparse
import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hospital_system.settings")
django.setup()

from core_patients.models import StaffProfile
from triage_queue.services import FlowError
from wards import services
from wards.models import Bed


def main():
    parser = argparse.ArgumentParser(description="Sign off cleaned beds as vacant (ready).")
    parser.add_argument("--actor", required=True,
                        help="Username of the nurse signing off the beds. Recorded in the bed history.")
    parser.add_argument("--ward", help="Only beds in this ward (exact name).")
    parser.add_argument("--apply", action="store_true",
                        help="Make the changes. Without this flag the script only lists the beds.")
    args = parser.parse_args()

    try:
        actor = StaffProfile.objects.select_related("user").get(user__username=args.actor)
    except StaffProfile.DoesNotExist:
        sys.exit(f"No staff profile found for username '{args.actor}'.")

    beds = Bed.objects.filter(status="CLEANING").select_related("ward").order_by("ward__name", "code")
    if args.ward:
        beds = beds.filter(ward__name=args.ward)
    beds = list(beds)

    if not beds:
        print("No beds are waiting for cleaning sign-off.")
        return

    print(f"{len(beds)} bed(s) waiting for sign-off:")
    for bed in beds:
        since = bed.status_changed_at.strftime("%Y-%m-%d %H:%M") if bed.status_changed_at else "unknown"
        print(f"  {bed.ward.name} · Bed {bed.code}  (cleaning since {since})")

    if not args.apply:
        print("\nDry run. Nothing was changed. Add --apply to mark these beds vacant.")
        return

    done, skipped = 0, 0
    for bed in beds:
        try:
            services.change_status(bed_id=bed.id, to_status="VACANT", staff=actor)
            done += 1
        except FlowError as exc:
            skipped += 1
            print(f"  Skipped {bed.code}: {exc}")

    print(f"\nMarked {done} bed(s) vacant as {actor.user.get_full_name() or actor.user.username}. Skipped {skipped}.")


if __name__ == "__main__":
    main()
