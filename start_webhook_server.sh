#!/bin/bash
###############################################################################
# Скрипт запуска Twilio Webhook Server
# Управляет Flask сервером в фоновом режиме
###############################################################################

BASE_DIR="/home/ubuntu/reminder_daemon"
LOG_DIR="$BASE_DIR/logs"
PID_FILE="$BASE_DIR/webhook_server.pid"
LOG_FILE="$LOG_DIR/webhook_server.log"
PYTHON_SCRIPT="$BASE_DIR/webhook_server.py"

# Создаем директорию логов если не существует
mkdir -p "$LOG_DIR"

# Цвета для вывода
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Функция для логирования
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1"
}

warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1"
}

# Функция проверки статуса
check_status() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            log "Webhook сервер запущен (PID: $PID)"
            return 0
        else
            warning "PID файл существует, но процесс не найден"
            rm -f "$PID_FILE"
            return 1
        fi
    else
        log "Webhook сервер не запущен"
        return 1
    fi
}

# Функция запуска сервера
start_server() {
    log "Запуск Twilio Webhook Server..."
    
    # Проверяем не запущен ли уже
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            warning "Сервер уже запущен (PID: $PID)"
            return 1
        else
            warning "Удаление устаревшего PID файла"
            rm -f "$PID_FILE"
        fi
    fi
    
    # Проверяем существование скрипта
    if [ ! -f "$PYTHON_SCRIPT" ]; then
        error "Python скрипт не найден: $PYTHON_SCRIPT"
        return 1
    fi
    
    # Запускаем сервер в фоновом режиме
    cd "$BASE_DIR"
    nohup python3 "$PYTHON_SCRIPT" >> "$LOG_FILE" 2>&1 &
    PID=$!
    
    # Сохраняем PID
    echo $PID > "$PID_FILE"
    
    # Ждем немного и проверяем что процесс запустился
    sleep 2
    
    if ps -p "$PID" > /dev/null 2>&1; then
        log "✅ Webhook сервер успешно запущен (PID: $PID)"
        log "📝 Логи: $LOG_FILE"
        log "🌐 URL: http://localhost:5000"
        log ""
        log "Для публичного доступа используйте ngrok:"
        log "  ngrok http 5000"
        return 0
    else
        error "Не удалось запустить сервер"
        rm -f "$PID_FILE"
        return 1
    fi
}

# Функция остановки сервера
stop_server() {
    log "Остановка Webhook сервера..."
    
    if [ ! -f "$PID_FILE" ]; then
        warning "Сервер не запущен (PID файл не найден)"
        return 1
    fi
    
    PID=$(cat "$PID_FILE")
    
    if ps -p "$PID" > /dev/null 2>&1; then
        log "Отправка сигнала SIGTERM процессу $PID"
        kill "$PID"
        
        # Ждем завершения процесса (до 10 секунд)
        for i in {1..10}; do
            if ! ps -p "$PID" > /dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        
        # Если процесс все еще работает, используем SIGKILL
        if ps -p "$PID" > /dev/null 2>&1; then
            warning "Процесс не завершился, используем SIGKILL"
            kill -9 "$PID"
            sleep 1
        fi
        
        if ! ps -p "$PID" > /dev/null 2>&1; then
            log "✅ Сервер успешно остановлен"
            rm -f "$PID_FILE"
            return 0
        else
            error "Не удалось остановить сервер"
            return 1
        fi
    else
        warning "Процесс не найден, удаление PID файла"
        rm -f "$PID_FILE"
        return 1
    fi
}

# Функция перезапуска
restart_server() {
    log "Перезапуск Webhook сервера..."
    stop_server
    sleep 2
    start_server
}

# Функция показа логов
show_logs() {
    if [ -f "$LOG_FILE" ]; then
        log "Последние 50 строк логов:"
        echo "-------------------------------------------"
        tail -n 50 "$LOG_FILE"
    else
        warning "Файл логов не найден: $LOG_FILE"
    fi
}

# Функция живых логов
follow_logs() {
    if [ -f "$LOG_FILE" ]; then
        log "Отслеживание логов (Ctrl+C для выхода):"
        echo "-------------------------------------------"
        tail -f "$LOG_FILE"
    else
        warning "Файл логов не найден: $LOG_FILE"
    fi
}

# Показ помощи
show_help() {
    cat << EOF
Usage: $0 {start|stop|restart|status|logs|follow}

Команды:
  start     - Запустить webhook сервер
  stop      - Остановить webhook сервер
  restart   - Перезапустить webhook сервер
  status    - Проверить статус сервера
  logs      - Показать последние логи
  follow    - Отслеживать логи в реальном времени

Примеры:
  $0 start          # Запуск сервера
  $0 status         # Проверка статуса
  $0 logs           # Просмотр логов
  $0 follow         # Живое отслеживание логов

Файлы:
  PID: $PID_FILE
  Logs: $LOG_FILE
  Script: $PYTHON_SCRIPT

EOF
}

# Основная логика
case "$1" in
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    restart)
        restart_server
        ;;
    status)
        check_status
        ;;
    logs)
        show_logs
        ;;
    follow)
        follow_logs
        ;;
    *)
        show_help
        exit 1
        ;;
esac

exit 0
