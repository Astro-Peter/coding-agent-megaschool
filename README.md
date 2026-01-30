# Megaschool Coding Agents

Автоматизированная система агентов SDLC для GitHub, которая охватывает полный цикл разработки ПО: от анализа задач до написания кода и ревью.

## Обзор

Система автоматизирует жизненный цикл разработки ПО с помощью ИИ-агентов:

1. **Planner Agent** — анализирует задачи и создаёт планы реализации
2. **Coder Agent** — реализует изменения в коде и создаёт pull request'ы (CLI-инструмент)
3. **Reviewer Agent** — ревьюит PR, анализирует результаты CI и даёт обратную связь (запускается в GitHub Actions)
4. **CI Fixer Agent** — Анализирует и показывает ошибки CI для Coder agent (линтинг, типы, тесты)

## Быстрый старт

### Вариант 1: GitHub Actions (рекомендуется)

Система может работать полностью в GitHub Actions:

1. Настройте секреты репозитория (см. ниже)
2. Создайте issue с описанием задачи
3. Workflow автоматически:
   - Запускает Planner Agent при создании issue
   - Запускает Coder Agent для реализации плана
   - Запускает Reviewer Agent при создании PR
   - Повторяет цикл до approve или достижения максимума итераций

## Использование в других репозиториях (Reusable Workflows)

Вы можете использовать SDLC-агенты в любом репозитории без копирования кода агентов.

#### Примеры Workflows (`examples/workflows/`)

| Файл | Описание | Триггер |
|------|----------|---------|
| `sdlc-agent.yml` | Основной SDLC workflow: Planner → Coder | Метка `agent` на issue |
| `sdlc-ci-fixer.yml` | Мониторинг CI: анализ ошибок → автоисправление | PR создан/обновлён |
| `sdlc-pr-fix.yml` | Исправление PR по комментариям | Метка `fix-issues` на PR |
| `sdlc-reviewer.yml` | Ревью PR → автоисправление при запросе изменений | Метка `agent-review` на PR |

#### Быстрая настройка

1. **Скопируйте нужные workflow** в целевой репозиторий:

```bash
# В вашем целевом репозитории
mkdir -p .github/workflows

# Основной SDLC workflow (Planner + Coder)
curl -o .github/workflows/sdlc-agent.yml \
  https://raw.githubusercontent.com/Astro-Peter/coding-agent-megaschool/main/examples/workflows/sdlc-agent.yml

# CI мониторинг и автоисправление (опционально)
curl -o .github/workflows/sdlc-ci-fixer.yml \
  https://raw.githubusercontent.com/Astro-Peter/coding-agent-megaschool/main/examples/workflows/sdlc-ci-fixer.yml

# Ревьюер (опционально)
curl -o .github/workflows/sdlc-reviewer.yml \
  https://raw.githubusercontent.com/Astro-Peter/coding-agent-megaschool/main/examples/workflows/sdlc-reviewer.yml

# PR фиксер (опционально)
curl -o .github/workflows/sdlc-pr-fix.yml \
  https://raw.githubusercontent.com/Astro-Peter/coding-agent-megaschool/main/examples/workflows/sdlc-pr-fix.yml
```

2. **Добавьте секреты** в целевой репозиторий (`Settings > Secrets > Actions`):
   - `GH_TOKEN`: GitHub Personal Access Token с правами `repo`
   - `LLM_API_TOKEN`: Ваш API-ключ LLM

3. **Опциональные переменные** (`Settings > Variables > Actions`):
   - `LLM_API_URL`: URL API-эндпоинта (по умолчанию: `https://openrouter.ai/api/v1`)
   - `LLM_MODEL`: Название модели (по умолчанию: `gpt-4o-mini`)
   - `LLM_MODEL_STRUCTURED`: Модель для структурированного вывода (для reviewer/ci-fixer)

4. **Используйте:**
   - Создайте issue и добавьте метку `agent` — запустится Planner → Coder
   - CI мониторинг запустится автоматически при создании PR
   - При провале CI — агент проанализирует ошибки и исправит
   - При успехе CI — добавится метка `agent-review` для ревью

#### Полный цикл автоматизации

```
Issue + метка "agent"
       │
       ▼
   Planner Agent ──► создаёт план в комментарии
       │
       ▼
   Coder Agent ──► создаёт PR с реализацией
       │
       ▼
┌──► CI проверки ◄──────────────────────────┐
│      │                                     │
│      ▼                                     │
│   CI Monitor                               │
│      │                                     │
│      ├─── провал ──► CI Fixer ──► коммит ──┘
│      │
│      └─── успех ──► добавляет "agent-review"
│                           │
│                           ▼
│                    Reviewer Agent
│                           │
│      ┌────────────────────┴────────────────┐
│      │                                     │
│      ▼                                     ▼
│   APPROVED ──► готов              CHANGES_REQUESTED
│   к мёрджу                                 │
│                                            ▼
└────────────────────────────── Coder Agent исправляет
```

**Примечание:** Вам нужен Personal Access Token (а не стандартный `GITHUB_TOKEN`), потому что reusable workflows запускаются в контексте репозитория megaschool и нуждаются в доступе к вашему целевому репозиторию.

## GitHub Actions Workflows

### Основные Workflows

| Workflow | Файл | Описание |
|----------|------|----------|
| **Issue Workflow** | `issue.yml` | Запускает Planner и Coder при создании issue |
| **PR Workflow** | `pr.yml` | Запускает Reviewer Agent при создании/обновлении PR |
| **PR Fix Workflow** | `pr-fix.yml` | Запускает Coder для исправления проблем по метке `fix-issues` |
| **CI Workflow** | `ci.yml` | Проверки качества кода (ruff, black, mypy, pytest) |

### Reusable Workflows

Переиспользуемые workflows для интеграции в другие репозитории:

| Workflow | Файл | Описание |
|----------|------|----------|
| **Reusable Planner** | `reusable-planner.yml` | Анализ issue и создание плана |
| **Reusable Coder** | `reusable-coder.yml` | Реализация плана в коде |
| **Reusable Reviewer** | `reusable-reviewer.yml` | Ревью PR и анализ CI |
| **Reusable PR Fix** | `reusable-pr-fix.yml` | Исправление проблем в PR |

### CI Workflow (`.github/workflows/ci.yml`)

Запускает три параллельных job'а:
- **lint**: Проверка ruff и black (форматирование)
- **typecheck**: Проверка типов mypy
- **test**: Запуск pytest

Триггеры:
- **Создание/обновление PR**: Запускает все проверки
- **Push в main/master**: Запускает все проверки

## Конфигурация

### Обязательные секреты

Настройте их в настройках репозитория (`Settings > Secrets and variables > Actions`):

| Секрет | Описание |
|--------|----------|
| `LLM_API_TOKEN` | API-токен для LLM-провайдера (OpenAI и т.д.) |

Примечание: Workflows используют встроенный секрет GitHub `GITHUB_TOKEN`, маппящийся на `GH_TOKEN` для агентов.

### Опциональные переменные

Настройте в `Settings > Secrets and variables > Actions > Variables`:

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `LLM_API_URL` | URL API-эндпоинта | `https://openrouter.ai/api/v1` |
| `LLM_MODEL` | Название модели | `gpt-4o-mini` |

Для локального запуска настройте .env как в ./.github/workflows/reusable-coder.yml

## Лимиты итераций

Система имеет встроенные защиты от бесконечных циклов:

- **Coder Agent**: Максимум 5 итераций разработки (`MAX_DEV_ITERATIONS`)
- **Reviewer Agent**: Максимум 5 итераций ревью до принудительного approve (`MAX_ITERATIONS`)
- **Agent Loop**: Максимум 50 вызовов LLM за один запуск агента (`MAX_AGENT_ITERATIONS`)

После достижения максимума итераций Reviewer принудительно approve'ит с предупреждениями.

## Разработка

### Установка dev-зависимостей

```bash
pip install -e ".[dev]"
```

### Запуск проверок качества

```bash
# Линтинг
ruff check github_agents/ tests/

# Форматирование
black github_agents/ tests/

# Проверка типов
mypy github_agents/ --ignore-missing-imports

# Тесты
pytest tests/ -v --tb=short
```

## Структура проекта

```
megaschool/
├── .github/workflows/           # GitHub Actions workflows
│   ├── issue.yml                # Issue/Planner/Coder workflow
│   ├── pr.yml                   # PR Review workflow
│   ├── pr-fix.yml               # Исправление проблем в PR
│   ├── ci.yml                   # Проверки качества
│   ├── reusable-planner.yml     # Переиспользуемый Planner
│   ├── reusable-coder.yml       # Переиспользуемый Coder
│   ├── reusable-reviewer.yml    # Переиспользуемый Reviewer
│   └── reusable-pr-fix.yml      # Переиспользуемый PR Fix
├── github_agents/               # Код агентов
│   ├── common/                  # Общие утилиты
│   │   ├── github_client.py     # Клиент GitHub API
│   │   ├── config.py            # Конфигурация
│   │   ├── context.py           # Контекст выполнения
│   │   ├── tools.py             # Инструменты агентов
│   │   ├── sdk_config.py        # Конфигурация SDK
│   │   └── code_index.py        # Индексация кода
│   ├── planner_agent/           # Анализ задач и планирование
│   │   ├── agent.py
│   │   └── prompts.py
│   ├── coder_agent/             # Реализация кода
│   │   ├── agent.py
│   │   ├── messages.py
│   │   ├── prompts.py
│   │   ├── run_from_plan.py     # Запуск из плана
│   │   ├── run_from_pr_comments.py  # Запуск для исправлений
│   │   └── runner_utils.py
│   ├── reviewer_agent/          # Ревью PR
│   │   ├── agent.py
│   │   └── prompts.py
│   ├── ci_fixer_agent/          # Исправление CI ошибок
│   │   ├── agent.py
│   │   └── prompts.py
│   └── orchestrator.py          # Координация агентов
├── configs/                     # Файлы конфигурации
│   ├── logging.yaml             # Настройки логирования
│   └── settings.yaml            # Общие настройки
├── examples/                    # Примеры для других репозиториев
│   └── workflows/               # Примеры workflows
│       ├── sdlc-agent.yml       # Plan + Code workflow
│       ├── sdlc-ci-fixer.yml    # CI fixer workflow
│       ├── sdlc-pr-fix.yml      # PR fix workflow
│       └── sdlc-reviewer.yml    # Reviewer workflow
├── tests/                       # Тесты
│   ├── test_issue_parsing.py
│   └── test_review_report.py
├── .env.example                 # Пример переменных окружения
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## Примеры использования

### Создание Issue

Создайте issue с чётким описанием задачи:

```
Заголовок: Добавить эндпоинт аутентификации пользователя

Описание:
Реализовать эндпоинт /login, который:
- Принимает имя пользователя и пароль
- Валидирует учётные данные
- Возвращает JWT-токен при успехе
- Возвращает 401 при ошибке
```

Система выполнит:
1. Planner Agent анализирует issue и создаёт план реализации
2. Coder Agent реализует изменения и создаёт PR
3. CI запускает проверки качества -> при фейле CI Agent анализирует их и возвращаемся во второй шаг
4. Reviewer Agent ревьюит PR и результаты CI
5. При обнаружении проблем Coder Agent исправляет их
6. Повторяет до approve

### Пример комментария Reviewer к PR

Reviewer Agent публикует:
- **PR Review**: Формальный GitHub-ревью (APPROVE или REQUEST_CHANGES)
- **PR Comment**: Детальный фидбек с машиночитаемыми данными

## Решение проблем

### Агент не срабатывает

- Проверьте, что секрет `LLM_API_TOKEN` установлен
- Убедитесь, что `GH_TOKEN` имеет права на запись
- Проверьте логи Actions на наличие ошибок

### Защита от бесконечных циклов

Система автоматически останавливается после максимума итераций. Проверьте:
- Метки issue `iteration-N` для просмотра текущего счётчика
- Комментарии к PR для сообщений о принудительном approve
