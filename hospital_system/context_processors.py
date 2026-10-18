from .permissions import caps_for


def permissions(request):
    return {"caps": caps_for(request.user)}
