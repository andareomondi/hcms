from .navigation import build_nav
from .permissions import caps_for


def permissions(request):
    caps = caps_for(request.user)
    nav_groups = build_nav(request, caps) if request.user.is_authenticated else []
    return {"caps": caps, "nav_groups": nav_groups}
