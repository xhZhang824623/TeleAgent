from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from . import auth_views

urlpatterns = [
    path("register/", csrf_exempt(auth_views.RegisterView.as_view()), name="auth-register"),
    path("login/", csrf_exempt(auth_views.LoginView.as_view()), name="auth-login"),
    path("client-login/", csrf_exempt(auth_views.ClientLoginView.as_view()), name="auth-client-login"),
    path("me/", auth_views.MeView.as_view(), name="auth-me"),
]
