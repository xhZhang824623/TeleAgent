import threading
from typing import Callable, Optional

try:
    from LocalBroker.broker_api import AuthError, client_login
except ModuleNotFoundError:
    from broker_api import AuthError, client_login


class CloudSessionManager:
    """
    线程安全：token 的读取/刷新用锁保护（仅锁住登录这段，不锁实际 HTTP 调用，
    以免并发执行被串行化）。401 重登采用双检，避免多个并发 worker 同时重复登录。
    """

    def __init__(
        self,
        *,
        api_base: str,
        credential_id: str,
        secret_key: str,
        token: Optional[str] = None,
        login_fn: Callable = client_login,
        save_token_fn: Optional[Callable[[str, str], None]] = None,
    ):
        self._api_base = api_base.rstrip("/")
        self._credential_id = credential_id.strip()
        self._secret_key = secret_key
        self._token = token
        self._login_fn = login_fn
        self._save_token_fn = save_token_fn
        self._lock = threading.Lock()

    @property
    def token(self) -> Optional[str]:
        return self._token

    def _login_locked(self) -> str:
        out = self._login_fn(self._credential_id, self._secret_key, base=self._api_base)
        token = out.get("token")
        if not token:
            raise RuntimeError("client login returned no token")
        self._token = token
        if self._save_token_fn:
            self._save_token_fn(token, out.get("email", ""))
        return token

    def login(self) -> str:
        with self._lock:
            return self._login_locked()

    def _ensure_token(self) -> str:
        """首次取 token：双检，多个并发线程只触发一次初始登录。"""
        with self._lock:
            if self._token:
                return self._token
            return self._login_locked()

    def _refresh_if_stale(self, used_token: Optional[str]) -> str:
        """401 后刷新 token：只有第一个发现 token 失效的线程实际重登，其余复用新 token。"""
        with self._lock:
            if self._token and self._token != used_token:
                return self._token  # 已有别的线程刷新过
            return self._login_locked()

    def call(self, fn: Callable, *args, **kwargs):
        token = self._ensure_token()
        kwargs["base"] = kwargs.get("base") or self._api_base
        kwargs["token"] = token
        try:
            return fn(*args, **kwargs)
        except AuthError:
            new_token = self._refresh_if_stale(token)
            kwargs["token"] = new_token
            return fn(*args, **kwargs)
