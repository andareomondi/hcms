
class StaffProfileMiddleware:
    """
    Attaches the user's StaffProfile directly to the request object 
    so request.staff_profile is globally accessible for staff members.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            request.staff_profile = getattr(request.user, "staff_profile", None)
        else:
            request.staff_profile = None
        return self.get_response(request)
