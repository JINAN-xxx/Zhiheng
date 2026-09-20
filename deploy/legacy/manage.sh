#!/bin/sh
set -eu

script_directory=$(CDPATH= cd "$(dirname "$0")" && pwd)
repository_root=$(CDPATH= cd "$script_directory/../.." && pwd)
compose_file=${RUOYI_COMPOSE_FILE:-"$repository_root/docker-compose.pg.yml"}
compose_project=${RUOYI_COMPOSE_PROJECT:-ruoyi-vue3-fastapi}
platform_environment=${RUOYI_ENV_FILE:-"$repository_root/.env"}
docker_command=${DOCKER_COMMAND:-docker}
compose_command=${COMPOSE_COMMAND:-}
compose_kind=

usage() {
    cat <<'EOF'
智衡平台部署管理

用法：
  ./deploy/legacy/manage.sh init       首次初始化密钥、数据库并构建启动
  ./deploy/legacy/manage.sh start      启动已有服务
  ./deploy/legacy/manage.sh restart    重启已有服务
  ./deploy/legacy/manage.sh stop       停止服务并保留数据
  ./deploy/legacy/manage.sh status     查看服务状态
  ./deploy/legacy/manage.sh logs       查看日志（可追加 --tail=100 等参数）
  ./deploy/legacy/manage.sh update     重新构建镜像并启动
  ./deploy/legacy/manage.sh help       显示此帮助

环境变量：
  RUOYI_COMPOSE_FILE    自定义 Compose 文件路径
  RUOYI_ENV_FILE        自定义环境文件路径
  DOCKER_COMMAND        Docker 命令，默认 docker
  COMPOSE_COMMAND       Compose 命令，默认优先 docker compose
  RUOYI_COMPOSE_PROJECT Compose 项目名，默认 ruoyi-vue3-fastapi
EOF
}

fail() {
    printf '错误：%s\n' "$1" >&2
    exit 1
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

require_docker() {
    command_exists "$docker_command" || fail "未找到 Docker 命令：$docker_command"
}

select_compose() {
    if [ -n "$compose_command" ]; then
        case "$compose_command" in
            'docker compose')
                compose_kind=docker
                ;;
            *)
                command_exists "$compose_command" || fail "未找到 Compose 命令：$compose_command"
                compose_kind=standalone
                ;;
        esac
        return
    fi

    if "$docker_command" compose version >/dev/null 2>&1; then
        compose_kind=docker
    elif command_exists docker-compose; then
        compose_command=docker-compose
        compose_kind=standalone
    else
        fail '未找到 Docker Compose，请安装 Docker Compose v2 或 docker-compose。'
    fi
}

require_files() {
    [ -f "$compose_file" ] || fail "Compose 文件不存在：$compose_file"
}

compose() {
    if [ "$compose_kind" = docker ]; then
        "$docker_command" compose --project-name "$compose_project" --env-file "$platform_environment" -f "$compose_file" "$@"
    else
        "$compose_command" --project-name "$compose_project" --env-file "$platform_environment" -f "$compose_file" "$@"
    fi
}

require_environment() {
    [ -f "$platform_environment" ] || fail "环境文件不存在：$platform_environment，请先执行 ./deploy/legacy/manage.sh init"
}

print_access_hint() {
    printf '%s\n' '平台地址：http://127.0.0.1:12580/'
    printf '%s\n' 'API 文档：http://127.0.0.1:12580/docker-api/docs'
}

init_platform() {
    [ -x "$repository_root/deploy/ruoyi/init-shared-postgres.sh" ] || \
        fail '数据库初始化脚本不存在或不可执行：deploy/ruoyi/init-shared-postgres.sh'
    "$repository_root/deploy/ruoyi/init-shared-postgres.sh" "$platform_environment"
    compose up -d --build
    print_access_hint
}

start_platform() {
    require_environment
    compose start
    print_access_hint
}

restart_platform() {
    require_environment
    compose restart
    print_access_hint
}

stop_platform() {
    require_environment
    compose stop
    printf '%s\n' '平台服务已停止，数据卷和容器仍然保留。'
}

status_platform() {
    require_environment
    compose ps
}

logs_platform() {
    require_environment
    compose logs -f "$@"
}

update_platform() {
    require_environment
    compose up -d --build
    print_access_hint
}

main() {
    action=${1:-help}
    shift || true

    case "$action" in
        help|-h|--help)
            usage
            ;;
        init|start|restart|stop|status|logs|update)
            require_docker
            select_compose
            require_files
            case "$action" in
                init)
                    init_platform
                    ;;
                start)
                    start_platform
                    ;;
                restart)
                    restart_platform
                    ;;
                stop)
                    stop_platform
                    ;;
                status)
                    status_platform
                    ;;
                logs)
                    logs_platform "$@"
                    ;;
                update)
                    update_platform
                    ;;
            esac
            ;;
        *)
            usage >&2
            fail "未知命令：$action"
            ;;
    esac
}

main "$@"
