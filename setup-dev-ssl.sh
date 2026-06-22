#!/bin/bash

# SSL证书生成脚本
# 用于本地开发环境生成自签名证书

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

# 检查OpenSSL是否安装
check_openssl() {
    if ! command -v openssl &> /dev/null; then
        print_error "OpenSSL 未安装！"
        echo "请先安装 OpenSSL："
        echo "  Ubuntu/Debian: sudo apt install openssl"
        echo "  CentOS/RHEL: sudo yum install openssl"
        echo "  macOS: brew install openssl"
        exit 1
    fi
    print_success "OpenSSL 已安装"
}

# 主函数
main() {
    echo "🔐 SSL证书生成工具，请确保脚本和项目compose文件夹在同一目录下"
    echo "=================="
    
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SSL_DIR="$SCRIPT_DIR/compose/nginx/ssl/dev"
    
    print_info "检查环境... 🔍 "
    check_openssl
    
    # 创建SSL目录
    print_info "创建SSL证书目录... 📁 "
    mkdir -p "$SSL_DIR"
    print_success "目录创建完成: $SSL_DIR"
    
    # 检查是否已存在证书
    if [ -f "$SSL_DIR/cert.pem" ] && [ -f "$SSL_DIR/key.pem" ]; then
        print_warning "检测到已存在的SSL证书文件... ⚠️ "
        read -p "是否要重新生成证书？(y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "跳过证书生成... ⚠️ "
            return 0
        fi
        print_info "备份现有证书... 🔄 "
        mv "$SSL_DIR/cert.pem" "$SSL_DIR/cert.pem.backup"
        mv "$SSL_DIR/key.pem" "$SSL_DIR/key.pem.backup"
        print_success "现有证书已备份... 🔄 "
    fi
    
    print_info "准备开始生成自签名SSL证书... 🔍 "
    read -p "按回车键继续，或按 Ctrl+C 取消: "
    echo
    
    print_info "开始生成自签名SSL证书... 📝 "
    echo
    
    # 生成私钥
    print_info "步骤 1/4: 生成私钥..."
    if openssl genrsa -out "$SSL_DIR/localhost.key" 2048 2>/dev/null; then
        print_success "私钥生成完成... 🔑 "
    else
        print_error "私钥生成失败... 🚨 "
        exit 1
    fi
    
    # 生成证书签名请求
    print_info "步骤 2/4: 生成证书签名请求..."
    if openssl req -new -key "$SSL_DIR/localhost.key" -out "$SSL_DIR/localhost.csr" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Development/CN=localhost" 2>/dev/null; then
        print_success "证书签名请求生成完成"
    else
        print_error "证书签名请求生成失败"
        exit 1
    fi
    
    # 生成自签名证书
    print_info "步骤 3/4: 生成自签名证书..."
    if openssl x509 -req -days 365 -in "$SSL_DIR/localhost.csr" \
        -signkey "$SSL_DIR/localhost.key" -out "$SSL_DIR/localhost.crt" 2>/dev/null; then
        print_success "自签名证书生成完成"
    else
        print_error "自签名证书生成失败"
        exit 1
    fi
    
    # 清理和重命名
    print_info "步骤 4/4: 整理证书文件..."
    rm -f "$SSL_DIR/localhost.csr"
    
    # 创建nginx需要的证书文件（复制并重命名）
    print_info "创建nginx需要的证书文件... 📝 "
    cp "$SSL_DIR/localhost.crt" "$SSL_DIR/cert.pem"
    cp "$SSL_DIR/localhost.key" "$SSL_DIR/key.pem"
    
    # 设置权限
    chmod 600 "$SSL_DIR/localhost.key" "$SSL_DIR/key.pem"
    chmod 644 "$SSL_DIR/localhost.crt" "$SSL_DIR/cert.pem"
    
    print_success "证书文件权限设置完成..."

    echo
    print_success "🎉 SSL证书生成完成！"
    echo
    print_info "证书文件位置："
    echo "  📄 证书文件: $SSL_DIR/cert.pem"
    echo "  🔑 私钥文件: $SSL_DIR/key.pem"
    echo
    print_info "证书信息："
    echo "  📅 有效期: 365天"
    echo "  🌐 域名: localhost"
    echo "  🏢 组织: Development"
    echo
    print_warning "重要提醒："
    echo "  • 这是自签名证书，浏览器会显示安全警告"
    echo "  • 在开发环境中，您可以手动信任此证书"
    echo "  • 生产环境请使用 Let's Encrypt 等权威证书"
    echo
    print_success "🎉 证书生成流程完成！"
}

# 错误处理
trap 'print_error "脚本执行失败，请检查错误信息"; exit 1' ERR

# 运行主函数
main "$@" 