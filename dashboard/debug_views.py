from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render
from django.urls import get_resolver


@staff_member_required
def url_explorer_view(request):
    """Lists all registered URL patterns in the Django project."""
    resolver = get_resolver()
    url_patterns = []

    def extract_urls(urlpatterns, prefix=""):
        for pattern in urlpatterns:
            if hasattr(pattern, "url_patterns"):
                # This is an included URLconf module
                extract_urls(pattern.url_patterns, prefix + str(pattern.pattern))
            else:
                url_patterns.append({
                    "pattern": prefix + str(pattern.pattern),
                    "name": getattr(pattern, "name", "N/A"),
                    "lookup": str(pattern.callback),
                })

    extract_urls(resolver.url_patterns)
    return render(request, "dashboard/url_explorer.html", {"url_patterns": url_patterns})
