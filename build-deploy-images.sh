#!/usr/bin/env bash
# TeleAgent 云端部署包构建脚本
# 用法：在项目根目录执行 ./build-deploy-images.sh
# 前置：将 SSL 证书放到 compose/nginx/ssl/deploy/cert.pem 与 key.pem
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info()    { echo -e "${BLUE}ℹ️  $1${NC}"; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }
print_error()   { echo -e "${RED}❌ $1${NC}"; }

# 证书可选：有则打包；无则部署时用自签名（适合先用 IP 访问、无域名场景）
CERT_FILE="./compose/nginx/ssl/deploy/cert.pem"
KEY_FILE="./compose/nginx/ssl/deploy/key.pem"
if [ -f "$CERT_FILE" ] && [ -f "$KEY_FILE" ]; then
  print_success "使用已有证书: $CERT_FILE"
  HAS_DEPLOY_CERT=1
else
  print_warning "未放置证书，部署时将自动生成自签名证书（用 IP 访问时浏览器会提示不安全，可继续访问）"
  print_info "  若需正式证书，可将 cert.pem / key.pem 放到: compose/nginx/ssl/deploy/"
  HAS_DEPLOY_CERT=0
  mkdir -p ./compose/nginx/ssl/deploy
fi

print_info "检查 Next 前端..."
if [ ! -d "./frontend-next" ]; then
  print_error "缺少 frontend-next 目录"
  exit 1
fi

print_info "开始构建部署镜像..."
cp -f env.deploy .env

print_info "停止现有容器..."
docker compose down -v || true

print_info "构建镜像（Nginx + Postgres + Django）..."
docker compose build

# 当前栈：Nginx + Postgres + Django + Next 前端
IMAGES=(nginx:1.27 postgres:17 djangoapps:latest teleagent-next:latest)

BUILD_DIR="build"
DEPLOY_NAME="teleAgent-server-deploy"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"
print_info "导出镜像..."
(cd "$BUILD_DIR" && docker save "${IMAGES[@]}" | gzip > docker-images.tar.gz)

print_info "打包部署文件（含前端、compose、证书、脚本）..."
mkdir -p "$BUILD_DIR/temp"
TEMP="$BUILD_DIR/temp"
cp docker-compose.yml "$TEMP/"
cp env.deploy "$TEMP/"
cp stop.sh "$TEMP/"
cp status-check.sh "$TEMP/"
cp start-deploy.sh "$TEMP/"
cp decompress-deploy.sh "$TEMP/"
cp -r compose "$TEMP/"

# 打包内容（前端由 Next 容器提供，无需再打包 frontend）；证书可选
TAR_ARGS=(docker-images.tar.gz docker-compose.yml env.deploy compose/nginx/conf.d/ compose/nginx/nginx.conf compose/nginx/ssl/deploy/ stop.sh status-check.sh start-deploy.sh decompress-deploy.sh)
[ "$HAS_DEPLOY_CERT" = 1 ] && TAR_ARGS+=(compose/nginx/ssl/deploy/cert.pem compose/nginx/ssl/deploy/key.pem)

cp "$BUILD_DIR/docker-images.tar.gz" "$TEMP/"
(cd "$TEMP" && tar -czf "../${DEPLOY_NAME}.tar.gz" "${TAR_ARGS[@]}")

# MD5
(cd "$BUILD_DIR" && md5sum "${DEPLOY_NAME}.tar.gz" > "${DEPLOY_NAME}.tar.gz.md5")

# 最终对外发布包（内层 tar + md5 + 解压脚本）
FINAL_PACKAGE="teleAgent-cloud-deploy.tar.gz"
(cd "$BUILD_DIR" && tar -czf "$FINAL_PACKAGE" "${DEPLOY_NAME}.tar.gz" "${DEPLOY_NAME}.tar.gz.md5" ../decompress-deploy.sh)

print_info "清理临时文件..."
rm -rf "$BUILD_DIR/temp"
rm -f "$BUILD_DIR/docker-images.tar.gz"
rm -f "$BUILD_DIR/${DEPLOY_NAME}.tar.gz"
rm -f "$BUILD_DIR/${DEPLOY_NAME}.tar.gz.md5"

echo ""
print_success "打包完成"
echo "📦 部署包: $BUILD_DIR/$FINAL_PACKAGE"
echo "📋 云端部署步骤："
echo "  1. 将 $FINAL_PACKAGE 上传到服务器"
echo "  2. 解压: tar -xzf $FINAL_PACKAGE"
echo "  3. 解压内包: tar -xzf ${DEPLOY_NAME}.tar.gz"
echo "  4. 按需修改 env.deploy（ALLOWED_HOSTS、CSRF_TRUSTED_ORIGINS、APP_DOMAIN、DATA_PREFIX、SECRET_KEY）"
echo "  5. 启动: ./start-deploy.sh"
echo ""