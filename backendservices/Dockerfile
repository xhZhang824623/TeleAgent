# 从仓库根构建 (context: ., dockerfile: backendservices/Dockerfile)
# 目录架构：backendservices/TeleAgent 为 Django 项目根
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 安装依赖
COPY backendservices/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# 复制 Django 项目（backendservices/TeleAgent）
COPY backendservices/TeleAgent /app/TeleAgent

WORKDIR /app/TeleAgent

# Default: run gunicorn (override in compose if needed)
ENV DJANGO_SETTINGS_MODULE=TeleAgent.settings
EXPOSE 9001

CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py collectstatic --noinput --clear || true && exec gunicorn TeleAgent.wsgi:application -b 0.0.0.0:9001 -w ${GUNICORN_WORKERS:-2} -k ${GUNICORN_WORKER_CLASS:-sync} --timeout ${GUNICORN_TIMEOUT:-30} --access-logfile - --error-logfile -"]
