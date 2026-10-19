from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie


@never_cache
@ensure_csrf_cookie
def app(request):
    """The whole web app is one page; it talks to /api/... for everything."""
    return render(request, "web/app.html", {"v": settings.MURIMI_ASSET_VERSION})


@never_cache
def service_worker(request):
    """Served from the site root so it may look after every page of the app (needs HTTPS or localhost)."""
    js = render_to_string("web/sw.js", {"v": settings.MURIMI_ASSET_VERSION})
    resp = HttpResponse(js, content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    return resp


def assetlinks(request):
    """/.well-known/assetlinks.json: tells Android that the Murimi APK may show this site full-screen."""
    if not settings.ANDROID_CERT_SHA256:
        return JsonResponse([], safe=False)
    return JsonResponse([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {"namespace": "android_app", "package_name": settings.ANDROID_PACKAGE,
                   "sha256_cert_fingerprints": settings.ANDROID_CERT_SHA256},
    }], safe=False)
