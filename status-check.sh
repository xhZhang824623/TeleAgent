#!/usr/bin/env bash
#
# status-check.sh — 查看 DAP 开发环境服务状态
#

set -e

# ---------------- 彩色输出 ----------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info()    { printf "${BLUE}ℹ️  %s${NC}\n"  "$*"; }
log_ok()      { printf "${GREEN}✅ %s${NC}\n"  "$*"; }
log_warn()    { printf "${YELLOW}⚠️  %s${NC}\n" "$*"; }
log_error()   { printf "${RED}❌ %s${NC}\n"   "$*"; }

# ---------------- 字符串宽度计算 ----------------
str_width() {
    local str="$1"
    # 移除ANSI颜色代码
    str=$(echo "$str" | sed 's/\x1b\[[0-9;]*m//g')
    # 计算实际显示宽度（中文字符算2个宽度）
    local width=0
    local len=${#str}
    for ((i=0; i<len; i++)); do
        local char="${str:$i:1}"
        if [[ $char =~ [\u4e00-\u9fff] ]]; then
            # 中文字符
            width=$((width + 2))
        else
            # 英文字符
            width=$((width + 1))
        fi
    done
    echo $width
}

# ---------------- 格式化输出 ----------------
format_cell() {
    local content="$1"
    local width="$2"
    local actual_width=$(str_width "$content")
    local padding=$((width - actual_width))
    
    if [[ $padding -gt 0 ]]; then
        printf "%s%*s" "$content" "$padding" ""
    else
        printf "%s" "$content"
    fi
}

# ---------------- 服务检查 ----------------
check_service() {
    local name=$1
    local cid status health

    cid=$(docker compose ps -q "$name" 2>/dev/null) || true
    if [[ -z $cid ]]; then
        printf "${RED}未运行${NC}"
        return 1
    fi

    health=$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "running")
    case $health in
        healthy)   printf "${GREEN}健康${NC}"   ;;
        unhealthy) printf "${RED}不健康${NC}" ;;
        *)         printf "${YELLOW}运行中${NC}" ;;
    esac
}

# ---------------- 端口检查 ----------------
check_port() {
    local port=$1 desc=$2
    if ss -tuln | grep -q ":$port "; then
        printf "${GREEN}已监听${NC}  (%s)\n" "$desc"
    else
        printf "${RED}未监听${NC}  (%s)\n" "$desc"
    fi
}

# ---------------- 主程序 ----------------
main() {
    echo "📊  DAP 开发环境服务状态"
    echo "========================"

    # Docker 运行检查
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker 未运行"
        exit 1
    fi

    log_info "服务状态："

    # 服务列表 (service_name:描述) — 使用 docker-compose 中定义的服务名
    services=(
      "nginxTeleAgent:Nginx 反向代理（含静态前端）"
      "TeleAgent:Django 应用"
      "DbTeleAgent:PostgreSQL 数据库"
    )

    printf "┌ %-25s ┬ %-10s ┬ %-20s ┐\n" "服务名称" "状态" "描述"
    printf "├─────────────────────────┼────────────┼────────────────────┤\n"
    for item in "${services[@]}"; do
        IFS=":" read -r name desc <<<"$item"
        status=$(check_service "$name")
        printf "│ %-25s │ %-10b │ %-20s │\n" "$name" "$status" "$desc"
    done
    printf "└─────────────────────────┴────────────┴────────────────────┘\n\n"

    # 端口检查
    log_info "端口监听："
    check_port 9020 "HTTP (外部)"
    check_port 9443 "HTTPS (外部)"
    echo

    # SSL 证书
    log_info "SSL 证书："
    SSL_DIR="$(pwd)/compose/nginx/ssl"
    if [[ -f $SSL_DIR/cert.pem && -f $SSL_DIR/key.pem ]]; then
        log_ok "证书文件存在"
        if command -v openssl >/dev/null; then
            expiry=$(openssl x509 -enddate -noout -in "$SSL_DIR/cert.pem" | cut -d= -f2)
            printf "${BLUE}📅  有效期至：%s${NC}\n" "$expiry"
        fi
    else
        log_error "缺少 cert.pem/key.pem"
    fi
    echo
    
    # 常用命令
    log_info "常用命令："
    echo " 🚀 启动服务  ./start-dev.sh"
    echo " 🛑 停止服务  ./stop.sh"
    echo " 📋 查看日志  docker compose logs -f"
    echo " 🔄 重启服务  docker compose restart"
    echo " 🧹 清理数据  docker compose down -v"
}

main "$@"
