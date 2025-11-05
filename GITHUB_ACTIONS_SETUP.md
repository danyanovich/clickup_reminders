# Настройка GitHub Actions для системы напоминаний

## Проблема
Ошибка `'clickup'` возникала из-за отсутствия ключа `clickup` в конфигурации при работе в GitHub Actions.

## Решение

### 1. Исправления в коде
- ✅ Исправлен `reminder_system.py` - добавлена поддержка legacy формата `config.json`
- ✅ Добавлены переменные окружения в workflow файл

### 2. Необходимые GitHub Secrets

Перейдите в **Settings → Secrets and variables → Actions** вашего репозитория и добавьте:

| Secret Name | Описание | Пример |
|------------|----------|--------|
| `CLICKUP_API_KEY` | API ключ ClickUp | `pk_...` |
| `CLICKUP_WORKSPACE_ID` | ID workspace/team | `90151494408` |
| `TWILIO_ACCOUNT_SID` | Twilio Account SID | `AC...` |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token | `...` |
| `TWILIO_PHONE_NUMBER` | Номер Twilio | `+1234567890` |
| `OPENAI_API_KEY` | OpenAI API ключ | `sk-...` |

### 3. Запуск workflow

После добавления секретов запустите workflow:

```bash
# Вариант 1: Через UI
# GitHub → Actions → Process Recordings → Run workflow

# Вариант 2: Через CLI (если установлен gh)
gh workflow run ".github/workflows/process-recordings.yml" -r main -f debug=false
```

### 4. Проверка работы

1. Перейдите во вкладку **Actions**
2. Откройте последний запуск **Process Recordings**
3. Проверьте, что шаг **Verify secrets** показывает ✅ для всех секретов
4. Проверьте логи шага **Run reminder system**

## Автоматический запуск

Workflow настроен на автоматический запуск:
- **Каждый час** (cron: `0 * * * *`)
- При **push в main** с изменениями в `.github/workflows/**`
- **Вручную** через workflow_dispatch

## Отладка

Если возникают ошибки:

1. Проверьте, что все секреты добавлены
2. Проверьте логи в Actions
3. Включите debug mode: `debug=true` при ручном запуске
