from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect


def csrf_failure(request, reason=""):
    if request.path.startswith("/admin/") and request.method == "POST":
        messages.warning(
            request,
            "This admin page was out of date, so no changes were made. "
            "Review the refreshed page and submit again.",
        )
        return HttpResponseRedirect(request.get_full_path())

    return HttpResponse("CSRF verification failed.", status=403)
