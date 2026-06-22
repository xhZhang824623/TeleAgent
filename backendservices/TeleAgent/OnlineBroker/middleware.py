"""
Broker/Auth API 请求强制豁免 CSRF（与 session 无关的 REST 调用）。
放在 CsrfViewMiddleware 之前：对 /api/broker/ 与 /api/auth/ 设置
request._dont_enforce_csrf_checks，避免依赖 URL 层包装细节。
"""


class BrokerCSRFExemptMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.path.startswith("/api/broker/") or request.path.startswith("/api/auth/"):
            request._dont_enforce_csrf_checks = True
        return None
