"""用户注册与登录（邮箱 + 密码）；LocalBroker 使用管理平台签发的 ID+SecretKey 登录。"""

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password, make_password
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.authtoken.models import Token

from .models import BrokerClientCredential, BrokerClientToken


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password")
        if not email or not password:
            return Response(
                {"detail": "email and password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if User.objects.filter(username=email).exists():
            return Response(
                {"detail": "A user with this email already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = User.objects.create_user(
            username=email,
            email=email,
            password=password,
        )
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {"token": token.key, "user_id": user.id, "email": user.email},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password")
        if not email or not password:
            return Response(
                {"detail": "email and password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user = authenticate(request, username=email, password=password)
        if user is None:
            return Response(
                {"detail": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {"token": token.key, "user_id": user.id, "email": user.email},
        )


class MeView(APIView):
    """当前登录用户信息（需带 Token）。"""

    def get(self, request):
        user = request.user
        return Response(
            {"user_id": user.id, "email": getattr(user, "email", user.username)},
        )


class ClientLoginView(APIView):
    """
    LocalBroker（PC）使用管理平台签发的 客户端 ID + Secret Key 登录。
    POST body: { "client_id": "uuid", "secret_key": "..." }
    返回与邮箱登录一致的 { token, user_id, email }，供后续 Broker API 使用。
    """
    permission_classes = [AllowAny]

    def post(self, request):
        client_id = (request.data.get("client_id") or "").strip()
        secret_key = request.data.get("secret_key") or ""
        if not client_id or not secret_key:
            return Response(
                {"detail": "client_id and secret_key are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            cred = BrokerClientCredential.objects.get(pk=client_id)
        except (BrokerClientCredential.DoesNotExist, ValueError):
            return Response(
                {"detail": "Invalid client_id or secret_key."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        if not check_password(secret_key, cred.secret_hash):
            return Response(
                {"detail": "Invalid client_id or secret_key."},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        user = cred.user
        # 签发该凭证专属的 broker Token（最小授权），而非用户的「主」Web Token。
        # 旋转：每次登录重置 key，旧 broker Token 失效；删除凭证则级联吊销。
        BrokerClientToken.objects.filter(credential=cred).delete()
        token = BrokerClientToken.objects.create(credential=cred)
        return Response(
            {"token": token.key, "user_id": user.id, "email": getattr(user, "email", user.username) or ""},
        )
