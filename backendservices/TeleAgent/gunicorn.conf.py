"""
Gunicorn 配置。

本服务有 SSE 长连接端点（/api/broker/tasks/<id>/stream/）。sync worker 下每条流会
独占一个 worker 进程，几条并发流就把后端打满。改用 gevent worker：单进程用协程承载
大量并发长连接；并通过 psycogreen 让 psycopg2 在 gevent 下协作式让出，避免 DB 查询
阻塞整个事件循环。

仍可用环境变量覆盖：GUNICORN_WORKER_CLASS / GUNICORN_WORKERS /
GUNICORN_WORKER_CONNECTIONS / GUNICORN_TIMEOUT。
"""
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:9001")
workers = int(os.environ.get("GUNICORN_WORKERS", "3"))
worker_class = os.environ.get("GUNICORN_WORKER_CLASS", "gevent")
# 每个 gevent worker 可并发承载的连接数（含 SSE 长连接）。
worker_connections = int(os.environ.get("GUNICORN_WORKER_CONNECTIONS", "1000"))
# SSE 是长连接，请求级超时不能太短，否则流会被 worker 杀掉。gevent 下用较大值。
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = int(os.environ.get("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.environ.get("GUNICORN_KEEPALIVE", "5"))

accesslog = "-"
errorlog = "-"


def post_fork(server, worker):
    """gevent worker 已对 socket/time 等做了 monkey-patch；这里再让 psycopg2 协作式让出。"""
    if "gevent" in worker_class:
        try:
            from psycogreen.gevent import patch_psycopg
            patch_psycopg()
            worker.log.info("psycogreen: psycopg2 patched for gevent")
        except ImportError:
            worker.log.warning("psycogreen 未安装，psycopg2 在 gevent 下可能阻塞事件循环")
