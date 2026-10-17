from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from dashboard.audit_actions import AuditAction
from dashboard.utils import log_action

from . import services
from .models import Room
from .services import DutyError


def _is_staff(user):
    return hasattr(user, "staff_profile") and user.staff_profile is not None


@login_required
@user_passes_test(_is_staff)
def clock_in_view(request):
    profile = request.staff_profile
    if services.current_session(profile):
        messages.info(request, "You are already clocked in.")
        return redirect("duty:board")

    rooms = services.available_rooms(department=profile.department)
    return render(request, "duty/clock_in.html", {"rooms": rooms, "profile": profile})


@login_required
@user_passes_test(_is_staff)
@require_POST
def clock_in_action(request):
    profile = request.staff_profile
    room = None
    room_id = request.POST.get("room_id")
    if room_id:
        room = Room.objects.filter(pk=room_id, is_active=True).first()
        if room is None:
            messages.error(request, "Select a valid room.")
            return redirect("duty:clock_in")
    try:
        services.clock_in(profile, room)
    except DutyError as exc:
        messages.error(request, str(exc))
        return redirect("duty:clock_in")

    log_action(request, AuditAction.DUTY_CLOCKED_IN,
               f"{profile} clocked in" + (f" in {room}." if room else "."))
    messages.success(request, "You are on duty." + (f" Room: {room.name}." if room else ""))
    return redirect("duty:board")


@login_required
@user_passes_test(_is_staff)
@require_POST
def clock_out_action(request):
    profile = request.staff_profile
    try:
        services.clock_out(profile)
    except DutyError as exc:
        messages.error(request, str(exc))
        return redirect("duty:board")

    log_action(request, AuditAction.DUTY_CLOCKED_OUT, f"{profile} clocked out.")
    messages.success(request, "You are now off duty.")
    return redirect("duty:clock_in")


@login_required
@user_passes_test(_is_staff)
@require_POST
def change_room_action(request):
    profile = request.staff_profile
    room = Room.objects.filter(pk=request.POST.get("room_id"), is_active=True).first()
    if room is None:
        messages.error(request, "Select a valid room.")
        return redirect("duty:board")
    try:
        services.change_room(profile, room)
    except DutyError as exc:
        messages.error(request, str(exc))
        return redirect("duty:board")

    log_action(request, AuditAction.DUTY_ROOM_CHANGED, f"{profile} moved to {room}.")
    messages.success(request, f"Moved to {room.name}.")
    return redirect("duty:board")


@login_required
def board_view(request):
    """Who is on duty and where. Any logged-in user can read this, including reception."""
    doctors = services.on_duty_staff(role="DOCTOR").order_by("user__last_name")
    my_session = services.current_session(getattr(request, "staff_profile", None))
    rooms = services.available_rooms() if my_session else []
    return render(request, "duty/board.html", {
        "doctors": doctors,
        "my_session": my_session,
        "rooms": rooms,
    })
