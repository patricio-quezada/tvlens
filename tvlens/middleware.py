"""Sends visitors on an old hostname to the canonical one."""

from django.conf import settings
from django.http import HttpResponseRedirect


class CanonicalHostMiddleware:
    """Redirect any request for a host in REDIRECT_HOSTS to CANONICAL_HOST.

    The app moved from app.tvlens.org to tvlens.org on Day 80. Old links,
    bookmarks and password reset emails still point at app.tvlens.org, so it
    keeps answering, with a redirect that keeps the path and query. /healthz
    is exempt: Fly's check must reach the app on whatever host it uses.

    307, not 301: it keeps the method, so a rating posted from a tab still
    open on the old host isn't turned into a GET and lost, and browsers don't
    cache it forever, so the move can still be rolled back. Make it a 308 once
    the move has held for a few weeks.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        canonical = settings.CANONICAL_HOST
        if (
            canonical
            and request.path != "/healthz"
            and request.get_host() in settings.REDIRECT_HOSTS
        ):
            response = HttpResponseRedirect(f"https://{canonical}{request.get_full_path()}")
            response.status_code = 307
            return response
        return self.get_response(request)
