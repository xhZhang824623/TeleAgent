"""
LocalBroker 客户端 Token 的 DRF 认证类。

与 DRF 自带的 TokenAuthentication 同形（Authorization: Token <key>），但 Token 来自
BrokerClientToken（每条 BrokerClientCredential 专属），认证后：
  - request.user = 该凭证归属的用户（credential.user）
  - request.auth = BrokerClientToken 实例（其 .credential 指向具体凭证）

关于顺序：DRF 在认证链中一旦某个认证类抛出 AuthenticationFailed 就立即终止，不会
再尝试后续认证类。由于本类与内置 TokenAuthentication 同用关键字 "Token"，必须把本类
排在 TokenAuthentication 之前，且在「key 不是 broker Token」时返回 None（放行而非抛错），
从而让链路回退到 TokenAuthentication 去校验用户主 Token。这样两类 Token 都能在所有
broker 端点工作。
"""
from rest_framework.authentication import TokenAuthentication

from .models import BrokerClientToken


class BrokerClientTokenAuthentication(TokenAuthentication):
    keyword = "Token"
    model = BrokerClientToken

    def authenticate_credentials(self, key):
        model = self.get_model()
        try:
            token = model.objects.select_related("credential__user").get(key=key)
        except model.DoesNotExist:
            # 不是 broker Token：返回 None 让认证链回退到 TokenAuthentication（用户主 Token）。
            return None
        user = token.credential.user
        if not user.is_active:
            return None
        return (user, token)
