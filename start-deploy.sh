#!/usr/bin/env bash
set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info "检查部署包内容... 🔍 "
REQUIRED_FILES=(
  "docker-images.tar.gz"
  "docker-compose.yml"
  "env.deploy"
  "compose/nginx/conf.d/"
  "compose/nginx/nginx.conf"
  "stop.sh"
  "status-check.sh"
)

print_info "验证部署包内容... 🔄 "
for file in "${REQUIRED_FILES[@]}"; do
  if [ ! -e "$file" ]; then
    print_error "缺少必要文件: $file... 💥 "
    exit 1
  fi
  print_success "$file"
done

print_info "加载Docker镜像... 🐳 "
if ! docker load -i docker-images.tar.gz; then
  print_error "镜像加载失败... 💥 "
  exit 1
fi
print_success "镜像加载完成... 🐳 "

# 设置环境变量
cp -f env.deploy .env
# 加载 DATA_PREFIX，并创建数据目录（bind mount 需要存在）
set -a
source .env 2>/dev/null || true
set +a
if [ -n "$DATA_PREFIX" ]; then
  print_info "创建数据目录: $DATA_PREFIX"
  mkdir -p "$DATA_PREFIX/logs/TeleAgent" "$DATA_PREFIX/staticfiles" "$DATA_PREFIX/postgres/data" "$DATA_PREFIX/postgres/backup"
fi

SSL_DIR="$(pwd)/compose/nginx/ssl"
DEPLOY_SSL_DIR="$(pwd)/compose/nginx/ssl/deploy"
mkdir -p "$SSL_DIR" "$DEPLOY_SSL_DIR"

# 证书：优先使用 deploy 目录下的；没有则生成自签名（适合无域名、用 IP 访问）
CERT_FILE=""
KEY_FILE=""
for cert in "$DEPLOY_SSL_DIR"/*.pem; do
  [ -f "$cert" ] && [[ "$cert" != *"key"* ]] && CERT_FILE="$cert" && break
done
for key in "$DEPLOY_SSL_DIR"/*.key; do
  [ -f "$key" ] && KEY_FILE="$key" && break
done
[ -z "$KEY_FILE" ] && for key in "$DEPLOY_SSL_DIR"/*.pem; do
  [ -f "$key" ] && [[ "$key" == *"key"* ]] && KEY_FILE="$key" && break
done

if [ -n "$CERT_FILE" ] && [ -n "$KEY_FILE" ]; then
  cp "$CERT_FILE" "$SSL_DIR/cert.pem"
  cp "$KEY_FILE" "$SSL_DIR/key.pem"
  print_success "已使用提供的证书"
else
  print_info "未提供证书，生成自签名证书（用 IP 访问时浏览器会提示不安全，可继续）..."
  if command -v openssl >/dev/null 2>&1; then
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
      -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
      -subj "/CN=localhost" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:0.0.0.0" 2>/dev/null || \
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
      -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
      -subj "/CN=localhost"
    print_success "自签名证书已生成"
  else
    print_error "未找到 openssl，无法生成证书。请安装 openssl 或将证书放到 $DEPLOY_SSL_DIR"
    exit 1
  fi
fi

print_info "关闭旧容器... 🛑 "
docker compose down -v || true

print_info "启动服务... 🔧 "
docker compose up -d

print_success "服务已启动。"
print_info "访问地址（将 <IP> 换成云服务器公网 IP 或域名）："
echo "  HTTP（无证书提示）: http://<IP>:9020/   Broker: http://<IP>:9020/broker"
echo "  HTTPS（自签名会提示不安全，可点继续）: https://<IP>:9443/   Broker: https://<IP>:9443/broker"
echo "  健康检查: http://<IP>:9020/health 或 https://<IP>:9443/health"
echo
print_info "常用命令："
echo "  查看日志: docker compose logs -f"
echo "  停止:     ./stop.sh 或 docker compose down"
echo "  状态:     ./status-check.sh"
print_success "部署完成。"