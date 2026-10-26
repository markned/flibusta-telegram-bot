# Codex Skills в Полке

Установка: проектные, версионируемые копии в `.agents/skills/<name>/SKILL.md`.
Это поддерживаемое repo discovery Codex, доступное после checkout в Cloud; plugin
bootstrap, MCP, hooks и runtime зависимости не устанавливаются. Подробности Codex:
[Skills](https://learn.chatgpt.com/docs/build-skills),
[Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Plugins](https://developers.openai.com/plugins/concepts/plugins).
Документация проверена 2026-10-26. Правила выбора являются инструкциями модели,
а не детерминированными runtime hooks; JSON-маршруты служат проверяемым каталогом.

## Вызов и активация

Явный вызов: `$search-regression Проверь регрессию поиска автора`.
Обычный запрос выбирает навык по короткому description; подробный SKILL.md и
его references читаются только при необходимости. Явный запрос имеет приоритет.
`grill-me`, `grill-with-docs`, `flibusta-grilling`, `setup-matt-pocock-skills`
имеют `allow_implicit_invocation: false`: интервью не запускается на мелких задачах.
Для сложной функции с неясным дизайном scoped-change предлагает явный вызов
`$grill-with-docs`; интервью не стартует скрыто. Никакое интервью при установке не проводилось.

## Внешние навыки

Все адаптации и оригинальные/установленные SHA256 зафиксированы в
[codex-upstream.json](codex-upstream.json). Это выборочная vendored-установка,
не одновременная установка plugins и skills. Сохранены полные локальные ресурсы
выбранных навыков. Лицензии и атрибуция: [licenses](licenses).

| Навык | Назначение / когда применять | Источник и commit | Зависимости |
|---|---|---|---|
| `grill-me` | Интервью по новой идее, только явно | [mattpocock](https://github.com/mattpocock/skills) `5beb35b040e3de97e54fa9e386d7e42d15c295c2` | flibusta-grilling |
| `flibusta-grilling` | Движок интервью; upstream имя grilling, проектный alias | [mattpocock](https://github.com/mattpocock/skills) `5beb35b040e3de97e54fa9e386d7e42d15c295c2` | Нет обязательных внешних инструментов |
| `grill-with-docs` | Интервью с глоссарием и ADR, только явно | [mattpocock](https://github.com/mattpocock/skills) `5beb35b040e3de97e54fa9e386d7e42d15c295c2` | flibusta-grilling; domain-modeling |
| `domain-modeling` | Уточнение терминов и значимых ADR | [mattpocock](https://github.com/mattpocock/skills) `5beb35b040e3de97e54fa9e386d7e42d15c295c2` | Нет обязательных внешних инструментов |
| `to-spec` | Синтез согласованной сложной функции в локальную спецификацию | [mattpocock](https://github.com/mattpocock/skills) `5beb35b040e3de97e54fa9e386d7e42d15c295c2` | docs/agents/issue-tracker.md; docs/agents/domain.md |
| `to-tickets` | Разбиение работы на несколько сессий, локальные tickets | [mattpocock](https://github.com/mattpocock/skills) `5beb35b040e3de97e54fa9e386d7e42d15c295c2` | docs/agents/issue-tracker.md; docs/agents/domain.md |
| `setup-matt-pocock-skills` | Повторная настройка tracker/domain, только явно | [mattpocock](https://github.com/mattpocock/skills) `5beb35b040e3de97e54fa9e386d7e42d15c295c2` | Нет обязательных внешних инструментов |
| `systematic-debugging` | Поиск причины воспроизводимого бага | [obra](https://github.com/obra/superpowers) `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b` | test-driven-development; verification-before-completion |
| `test-driven-development` | Красный/зелёный тест при изменении поведения | [obra](https://github.com/obra/superpowers) `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b` | Нет обязательных внешних инструментов |
| `verification-before-completion` | Проверки перед заявлением о завершении | [obra](https://github.com/obra/superpowers) `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b` | Нет обязательных внешних инструментов |
| `differential-review` | Ревью diff при изменении границ безопасности | [trailofbits](https://github.com/trailofbits/skills) `196a9073ab10d35bacf111f945e02c00b2c6883a` | Нет обязательных внешних инструментов |
| `modern-python` | Настройка Python tooling по запросу, без массовой миграции | [trailofbits](https://github.com/trailofbits/skills) `196a9073ab10d35bacf111f945e02c00b2c6883a` | Optional only: uv, ruff, ty, prek and security tools for requested tooling migrations |
| `supply-chain-risk-auditor` | Аудит зависимостей по запросу | [trailofbits](https://github.com/trailofbits/skills) `196a9073ab10d35bacf111f945e02c00b2c6883a` | Python >=3.11 standard library; Live audit: registry/advisory/GitHub network access; Optional authenticated gh and pip-audit/osv-scanner/npm/cargo-audit/bundler-audit for cross-checks; absence reported as coverage limit; Offline bundled script tests: pytest |
| `insecure-defaults` | Последовательный аудит небезопасных defaults | [trailofbits](https://github.com/trailofbits/skills) `196a9073ab10d35bacf111f945e02c00b2c6883a` | Нет обязательных внешних инструментов |

`grilling` уже существовал в пользовательских навыках хоста. Он сохранён без
изменений; проектная версия называется `flibusta-grilling`, чтобы избежать
одинаковых имён. Grill Me и Grill With Docs читают этот sibling напрямую;
Cloud не зависит от домашней директории пользователя. Других проектных дублей нет.

Matt: Claude Skill-tool вызовы заменены чтением sibling SKILL.md, обязательная
делегация — ограниченным локальным исследованием. Setup уже выполнен:
[локальный tracker](agents/issue-tracker.md), [single-context domain](agents/domain.md).
Термины/ADR создаются по мере реальных решений; triage skill не установлен.
Superpowers: только три выбранных навыка и их вспомогательные файлы; нет using-superpowers,
hooks, bootstrap или обязательной цепочки brainstorming/subagents.
Trail of Bits: upstream `insecure-defaults` в закреплённой версии — command и
multi-agent workflow, а не SKILL.md. Его материалы преобразованы в native serial skill;
JS workflow и массовые агенты не установлены. Это адаптация, не заявление о наличии
готового upstream Codex skill. Адаптированные материалы ToB сохраняют CC-BY-SA-4.0.

Supply-chain live audit требует сети к registry/advisories/GitHub; отсутствие gh,
pip-audit и прочих дополнительных сканеров отражается как ограничение покрытия.
Его offline script tests требуют только pytest, collector/render — stdlib Python.
Modern-python не устанавливает uv/ty/prek и не меняет менеджер проекта автоматически.
Ни один проектный test skill не требует реальных внешних сервисов.

## Проектные навыки

Источник: этот репозиторий, версия — commit PR; внешних зависимостей, кроме
существующего dev-окружения и команд Make, нет. Все доступны как `$имя`.

| Навык | Активация и пример | Проверки |
|---|---|---|
| scoped-change | Любая разработка: «Исправь обработчик» | diff → символы/imports → релевантный навык |
| search-regression | Поиск: «Автор+название даёт пусто» | make test-search, golden/cache/fallback/deadline |
| telegram-flow-test | aiogram handlers: «Проверь callback пагинации» | моки Telegram, routing/FSM/access/favorites/history |
| reader-delivery-test | Kindle/PocketBook: «Проверь ошибку SMTP» | make test-kindle, test_readers, conversion/cleanup |
| web-auth-and-eink | Web/auth: «Проверь replay кода» | test_web/test_web_devices, HTTP/session и старый WebKit |
| sqlite-migration-safe | Schema/repositories: «Добавь миграцию» | пустая/старая БД, повторы, сохранение данных, WAL |
| low-memory-audit | Ресурсы: «Проверь очередь на VPS» | bounded pools/tasks/cache/files, offline измерения |
| flibusta-parser-fixtures | Parser: «Добавь нестандартную разметку» | локальные HTML, transport mocks, covers/formats |
| add-command | Новая команда Telegram | router/menu/admin checks и telegram-flow-test |
| add-tests | Регрессия или acceptance criterion | существующие mocks/fixtures, focused tests → check |

Миграции: add-command и add-tests перенесены; add-db-migration объединён с
sqlite-migration-safe, fix-search-regression — с search-regression. Старые plain
`.codex/skills/*.md` удалены, aliases не создают второй механизм discovery.
Пять специализаций агентов перенесены из Markdown в `.codex/agents/*.toml`;
recommender-agent удалён, поскольку AI и scheduler уже отсутствуют в архитектуре.
По умолчанию работает один агент; настройка ограничивает параллельных подагентов
двумя, не запускает их автоматически. Дочерние агенты не создают дальнейшие уровни.

## Маршрутизация

[codex-routing.json](codex-routing.json) содержит маршруты и безопасные static smoke-примеры.
В scoped-change закреплены условия: только релевантные этапы, одна линия планирования.

- Сложная функция: явно grill-with-docs при неопределённом дизайне → to-spec →
  to-tickets только для нескольких сессий → scoped implementation с TDD → verification.
- Новая идея: явно grill-me.
- Баг: systematic-debugging → focused regression test → minimal fix → verification.
- Поиск: scoped-change → search-regression → verification.
- Читалки: scoped-change → reader-delivery-test → verification.
- Web auth: web-auth-and-eink → differential-review → verification.
- SQLite: sqlite-migration-safe → focused tests → verification.
- Производительность: low-memory-audit → focused measurements → verification.
- Текст/косметика: scoped-change → подходящая проверка, без интервью и security audit.

## Cloud, обновление и восстановление

После checkout ветки/PR выполните `bash .codex/cloud-setup.sh`; maintenance:
`bash .codex/cloud-maintenance.sh`. Оба устанавливают requirements.txt и отдельный
requirements-dev.txt в .venv. Production deploy устанавливает только requirements.txt.
Для тестов задайте `TELEGRAM_BOT_TOKEN=123456:dummy`; production .env не нужен.
Никаких runtime импортов из .agents нет. Новый Cloud checkout уже содержит все навыки.
В интерфейсе выберите этот checkout; при устаревшем каталоге перезапустите Codex.

Чтобы переустановить повреждённые навыки, восстановите `.agents/skills` и
`docs/codex-upstream.json` из проверенного commit этой ветки (`git restore --source=<commit> -- ...`,
предварительно сохранив свои изменения). Не запускайте npx/curl installer поверх vendor-копий.
Обновление: прочитайте manifest; клонируйте соответствующий официальный repository
во временную папку, checkout выбранного SHA, изучите diff SKILL.md, лицензии и dependencies.
Копируйте только нужные каталоги и их ресурсы, повторите документированные Codex-адаптации,
обновите commit и hashes в manifest, проверьте ссылки, тесты и native discovery.
Не заменяйте adaptation без проверки: upstream может вернуть Claude-only команды
или обязательные дополнительные агенты. Фиксируйте обновление отдельным PR.

## Проверки и диагностика

```
.venv/bin/python scripts/check_codex_skills.py
TELEGRAM_BOT_TOKEN=123456:dummy make test
TELEGRAM_BOT_TOKEN=123456:dummy make test-search
TELEGRAM_BOT_TOKEN=123456:dummy make test-kindle
make lint
TELEGRAM_BOT_TOKEN=123456:dummy make check
.venv/bin/python -m pytest -q .agents/skills/supply-chain-risk-auditor/scripts
```

Валидатор проверяет discovery-структуру, frontmatter, локальные references,
TOML агентов и static routing smoke; не выдаёт это за реальную активацию модели.
Для native discovery без LLM-вызова: `codex debug prompt-input 'Read-only search regression smoke'`
и проверьте каталог навыков (не публикуйте полный вывод: он содержит пользовательский контекст).
Explicit-only навыки обычно не присутствуют в implicit prompt-каталоге; их наличие
проверяйте через Skills selector / явный `$grill-with-docs` в новой сессии.
При отсутствии навыка проверьте cwd/repo root, SKILL.md и YAML, allow_implicit_invocation,
глобальные дубли и перезапуск. `.codex/instructions.md` является compatibility pointer,
не автоматически загружаемым Codex instruction-файлом; политика находится в AGENTS.md.

Ruff добавлен только в dev, закреплён 0.14.14. Начальные правила E9/F63/F7/F82
дополняют compileall; проверки не отключаются при находках. На исходном main Ruff
уже выявляет 4 F821 в app/main.py (515, 623, 802, 941), связанные с exception variable
в lambda. Поэтому make lint/check и новый CI lint остаются красными до отдельного
исправления production-кода; установка навыков не меняет бизнес-логику ради зелёного CI.

## Экономия токенов

Короткий AGENTS.md, точные descriptions и progressive disclosure; body/references
читаются по задаче. diff/symbol/import-based scope вместо чтения всего репозитория;
повторное использование выводов и существующих тестов, компактные логи и отчёты.
Нет принудительного bootstrap, дополнительных MCP/API, конкурирующих planners,
обязательных подагентов или повторных full reviews неизменённого кода. Интервью и
setup явно вызываются; tickets только за пределами одной сессии. Проценты экономии
не заявляются: измерения токенов не проводились.

## Результат установки (2026-10-26)

- Native app-server `skills/list` с forceReload обнаружил все 24 project skills,
  `scope=repo`, enabled=true, без ошибок; включая четыре explicit-only entrypoints.
- `codex debug prompt-input` подтвердил implicit-каталог с scoped-change/search-regression;
  explicit-only интервью в него не подмешиваются. Реальное интерактивное интервью
  намеренно не выполнялось; при первом использовании выберите ветку в новой сессии
  и вызовите `$grill-me` или `$grill-with-docs`.
- skill-creator quick_validate: 24/24. Локальный validator: 24 skills, 5 agents,
  references, lock hashes, зависимости и отсутствие циклов; его offline tests проходят.
- `TELEGRAM_BOT_TOKEN=123456:dummy make test`: 185 passed; test-search: 116 passed;
  test-kindle: 23 passed. Supply-chain bundled scripts: 105 passed.
- Независимый read-only smoke: дубликаты автор+название → search-regression;
  ложный SMTP success → reader-delivery-test; replay кода → web-auth-and-eink +
  differential-review; опечатка → focused UI verification; SQLite индекс →
  sqlite-migration-safe. Во всех случаях без интервью, tickets и дополнительных агентов.
- Ruff и make check: четыре исходные F821, воспроизведены на `git show HEAD:app/main.py`
  до изменений. Production исходники не менялись. Это известный blocker зелёного lint/CI.

Follow-up: the search-improvement branch binds those four retry-lambda exception
messages explicitly; its make lint/check pass without suppressing Ruff rules.
The blocker above records the original skills-installation baseline.
