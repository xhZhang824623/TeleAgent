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

# 部署包名（与 build-deploy-images.sh 中 DEPLOY_NAME 一致）
DEPLOY_PACKAGE="teleAgent-server-deploy.tar.gz"
if [ ! -f "$DEPLOY_PACKAGE" ]; then
  # 兼容旧包名
  if [ -f "sentireEcosystem-server-deploy.tar.gz" ]; then
    DEPLOY_PACKAGE="sentireEcosystem-server-deploy.tar.gz"
  else
    print_error "部署包不存在。请将 teleAgent-cloud-deploy.tar.gz 解压后在此目录执行本脚本。"
    exit 1
  fi
fi

print_info "检查部署包完整性... 🔍 "
MD5_FILE="${DEPLOY_PACKAGE}.md5"
if [ -f "$MD5_FILE" ]; then
  if ! md5sum -c "$MD5_FILE"; then
    print_error "MD5 校验失败，部署包可能已损坏。"
    exit 1
  fi
  print_success "MD5 校验通过。"
else
  print_warning "未找到 .md5 文件，跳过完整性检查。"
fi

print_info "解压部署包... 📦 "
tar -xzf "$DEPLOY_PACKAGE"

print_info "赋予脚本执行权限... 🔧 "
chmod +x start-deploy.sh
chmod +x stop.sh
chmod +x status-check.sh
print_success "权限设置完成。"

print_info "准备就绪。"
echo "------------------------------------------------------"
echo "  启动: ./start-deploy.sh  （或 sudo ./start-deploy.sh）"
echo "  停止: ./stop.sh"
echo "  状态: ./status-check.sh"
echo "------------------------------------------------------"