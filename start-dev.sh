#!/bin/bash

# 开发环境启动脚本

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

check_conflict_env() {
    if printenv | grep -q "^DEBUG="; then
        print_error "⚠️ 当前 shell 中已定义了 DEBUG=${DEBUG}，将会覆盖 .env 中的设置"
        echo "    请运行以下命令取消该变量："
        echo "    unset DEBUG"
        exit 1
    fi
}

# 检查Docker是否运行
check_docker() {
    if ! docker info &> /dev/null; then
        print_error "Docker 未运行！"
        echo "请先启动 Docker 服务："
        echo "  sudo systemctl start docker"
        echo "  或者启动 Docker Desktop"
        exit 1
    fi
    print_success "Docker 正在运行"
}

# 创建 bind-mount 数据目录（compose 的 volumes 用 o:bind，宿主目录必须先存在，否则挂载失败）
ensure_data_dirs() {
    set -a
    source .env 2>/dev/null || true
    set +a
    if [ -n "$DATA_PREFIX" ]; then
        print_info "创建数据目录: $DATA_PREFIX"
        mkdir -p "$DATA_PREFIX/logs/TeleAgent" "$DATA_PREFIX/staticfiles" "$DATA_PREFIX/media/TeleAgent" "$DATA_PREFIX/postgres/data" "$DATA_PREFIX/postgres/backup"
    fi
}

# 无域名/无证书时：自动生成自签名证书（HTTPS 可用，浏览器会提示不安全）
ensure_ssl_certs() {
    SSL_DIR="$(pwd)/compose/nginx/ssl"
    mkdir -p "$SSL_DIR"
    if [ ! -f "$SSL_DIR/cert.pem" ] || [ ! -f "$SSL_DIR/key.pem" ]; then
        print_info "未检测到证书，正在生成自签名证书（localhost，有效期 365 天）..."
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
            -subj "/CN=localhost" \
            -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" 2>/dev/null || \
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
            -subj "/CN=localhost"
        print_success "自签名证书已生成（浏览器访问 HTTPS 时会提示不安全，可忽略）"
    else
        print_success "已存在证书 $SSL_DIR"
    fi
}

detect_build_network() {
    # Allow user override (export DOCKER_BUILD_NETWORK=host/default/none...)
    if [ -n "${DOCKER_BUILD_NETWORK:-}" ]; then
        print_info "已检测到 DOCKER_BUILD_NETWORK=${DOCKER_BUILD_NETWORK}，将使用该设置进行镜像构建"
        return
    fi

    print_info "检查 Docker 容器出网/DNS（用于镜像构建阶段下载依赖）..."
    if docker run --rm python:3.12-slim python -c "import socket; socket.gethostbyname('deb.debian.org')" >/dev/null 2>&1; then
        export DOCKER_BUILD_NETWORK=default
        print_success "Docker 容器 DNS 正常（build 使用默认网络）"
    else
        export DOCKER_BUILD_NETWORK=host
        print_warning "Docker 容器当前无法解析外网域名（bridge 出网/DNS 异常）"
        print_info "将尝试使用 DOCKER_BUILD_NETWORK=host 进行构建（仅影响 build 阶段；Linux 环境支持）"
    fi
}

cp -f env.dev .env
print_success ".env文件已创建"

# 主函数
main() {
    echo "Sentire Ecosystem Backend 开发环境启动工具"
    echo "========================"
    
    print_info "检查环境..."
    check_conflict_env
    check_docker
    ensure_data_dirs
    ensure_ssl_certs
    detect_build_network

    print_info "停止已有开发环境服务..."
    docker compose down -v
    print_success "原有容器已停止并删除对应卷..."

    print_info "🚀启动开发环境服务..."
    echo

    # 使用开发环境配置启动服务
    if docker compose up -d --build; then
        print_success "服务启动成功！"
    else
        print_error "服务启动失败"
        exit 1
    fi
    
    echo
    print_success "🚀 开发环境启动完成！"
    print_info "访问地址（开发环境）："
    echo "  🌐 主应用: https://localhost:9443/  （或 http://localhost:9020/）"
    echo "  🔌 API服务: https://localhost:9443/api/"
    echo "  💚 健康检查: https://localhost:9443/health/"
    echo
    print_info "常用命令："
    echo "  📋 查看日志: docker compose logs -f"
    echo "  🛑 停止服务: docker compose down"
    echo "  🔄 重启服务: docker compose restart"
    echo "  🧹 清理数据: docker compose down -v"
    echo

    print_info "使用以下命令检查服务状态："
    echo "   ./status-check.sh"
    print_info "使用以下命令停止服务：" 
    echo "   ./stop.sh"
    echo
    
    print_success "🎉🎉🎉 开发环境启动完成！"
}

# 错误处理
trap 'print_error "启动失败，请检查错误信息"; exit 1' ERR

# 运行主函数
main "$@" 