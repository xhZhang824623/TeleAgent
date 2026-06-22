#!/bin/bash

# 开发环境停止脚本

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

# 主函数
main() {
    echo "DAP Server停止工具"
    echo "------------------------------------------------------"
    
    print_info "检查运行中的服务..."
    
    # 检查是否有服务在运行
    if ! docker compose ps | grep -q "Up"; then
        print_warning "没有发现运行中的服务"
        exit 0
    fi
    
    print_info "发现运行中的服务："
    docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
    echo
    
    print_warning "即将停止所有服务"
    read -p "是否继续？(Y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Nn]$ ]]; then
        print_info "操作已取消"
        exit 0
    fi
    
    print_info "停止服务..."
    
    # 停止服务
    if docker compose down -v; then
        print_success "服务停止成功！"
    else
        print_error "服务停止失败"
        exit 1
    fi
    
    echo
    print_info "服务状态："
    docker compose ps
    echo
    print_success "服务已停止！ 🛑🛑🛑🛑🛑"
}

# 错误处理
trap 'print_error "停止失败，请检查错误信息"; exit 1' ERR

# 运行主函数
main "$@" 