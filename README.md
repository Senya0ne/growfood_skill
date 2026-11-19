# 🍱 growfood_skill

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)]()

**Управляй рационом Grow Food из чата с AI‑агентом.**

🇬🇧 [English version](README.en.md)

---

## Что это

Открытый навык (skill) для AI‑агентов — [Hermes](https://github.com/NousResearch/hermes-agent), Claude Code, Antigravity, Codex, Cursor — который управляет подпиской [Grow Food](https://growfood.pro) через Telegram или чат.

Показывает меню, подбирает замены по твоим вкусам, меняет блюда одной командой, запоминает что нравится, собирает оценки съеденного — всё автоматически, без захода в приложение.

> [!NOTE]
> Grow Food — сервис доставки готовой еды, работает только в России (Москва, СПб и другие города).

---

## Возможности

- 📋 **Просмотр меню** — все активные заказы с иконками вкусов (❤️ / ⛔ / ⚠️ / 🆕)
- 🤖 **Умные предложения** — подбор замен по истории вкусов, без доплаты
- 🔄 **Замена в один клик** — `replace`, `swap`, `autofix`
- 🧠 **Память вкусов** — избранное, стоп‑лист, нелюбимые ингредиенты, заметки
- ⭐ **Оценки съеденного** — топ / норм / не зашло / надоело / аллергия / порция
- 🔔 **Ежедневные напоминания** — cron‑задача для агента
- 💰 **Работает на оплаченных заказах** — меняет ✏️ дни, показывает доплату/экономию

---

## Быстрый старт

```bash
# 1. Клонируй в папку навыков своего агента
git clone https://github.com/Senya0ne/growfood_skill.git \
  ~/.hermes/skills/growfood          # или другую, см. таблицу ниже

# 2. Получи токен из браузера (см. раздел «Токен»)

# 3. Сохрани токен
python3 ~/.hermes/skills/growfood/scripts/growfood.py set-token
# вставь токен → Enter

# 4. Готово!
python3 ~/.hermes/skills/growfood/scripts/growfood.py review
```

Удобный ярлык `gf`:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/gf <<'EOF'
#!/bin/sh
exec python3 "$HOME/.hermes/skills/growfood/scripts/growfood.py" "$@"
EOF
chmod +x ~/.local/bin/gf
# Теперь: gf review && gf suggest
```

---

## Установка по платформам

| Агент | Куда клонировать |
|---|---|
| **Hermes** | `~/.hermes/skills/growfood/` |
| **Claude Code** | `~/.claude/skills/growfood/` |
| **Antigravity** | `~/.gemini/config/skills/growfood/` |
| **Cursor** | скопируй `.cursorrules` в корень проекта |
| **Codex** | скопируй `AGENTS.md` в корень проекта |

Адаптеры для каждой платформы — в папке [`adapters/`](adapters/install.md).

> [!TIP]
> Работает на Linux, macOS и Raspberry Pi. Только стандартная библиотека Python 3.9+, установка пакетов не нужна.

---

## Токен

Grow Food защищает вход по SMS капчей — агент не может войти автоматически. Токен берётся из твоей сессии:

1. Открой [lk.growfood.pro](https://lk.growfood.pro) в браузере и войди обычным способом.
2. **F12** → **Network** → обнови страницу → кликни любой запрос к `admin.growfood.pro`.
3. В **Request Headers** скопируй значение `x-client-token`.
4. Выполни:
   ```bash
   gf set-token     # вставь токен → Enter
   ```

> [!IMPORTANT]
> - **Не нажимай «Выйти»** в веб‑кабинете — это отзовёт токен. Просто закрой вкладку.
> - Когда токен протухнет, агент напишет — повтори шаги 1–4.

---

## Команды

| Команда | Описание |
|---|---|
| `review` | Меню активных заказов: ❤️ ⛔ ⚠️ 🆕 🔁 ⏳ |
| `suggest [--save]` | Предложения замен по вкусам (без доплаты) |
| `apply-saved [номера…]` | Применить предложения: все или `1 3` |
| `alternatives --date D --meal N` | Чем заменить конкретное блюдо |
| `replace --date D --meal N --pack ID` | Заменить одно блюдо |
| `swap D:N:ID [D:N:ID …]` | Несколько замен разом |
| `revert --date D --meal N` | Вернуть стандартное блюдо |
| `autofix` | Применить все `suggest` (только бесплатные) |
| `pending` | Данные для напоминаний |
| `mark-reviewed` | Отметить ✏️ дни как показанные |
| `fav add\|rm ID` | Избранное |
| `stop add\|rm ID` | Стоп‑лист |
| `taste show` | Показать память вкусов |
| `taste ingredient "корень"` | Нелюбимый ингредиент (`свекл`, `гриб`) |
| `taste note "текст"` | Заметка о вкусах |
| `rate-tasks` | Блюда для оценки |
| `rate TASK love\|ok\|bad\|tired\|allergy\|portion` | Оценить |
| `orders` / `accept` / `drop` | Заказы / подтвердить / сбросить |
| `login --phone` / `--code` | Вход по SMS (если нет капчи) |
| `set-token` | Сохранить токен из браузера |

Все команды замены поддерживают `--dry-run` и `--allow-extra`.

---

## Как работает подбор замен

| Фактор | Баллы |
|---|---|
| ❤️ Нравится | **+3** |
| 🆕 Не оценено | **0** |
| ⚠️ Нелюбимый ингредиент | **−6** |
| ⛔ Стоп‑лист | **−10** |
| 🔁 Повтор блюда | **−1.5** за каждый |
| 🍽️ Близость по ккал | бонус |
| 💰 Доплата | штраф |

Замена предлагается, если текущее ⚠️/⛔ и есть лучше, или есть ❤️ с выигрышем ≥ 3 балла.

---

## Конфигурация

Файлы в `~/.config/growfood/` (права `600`):

| Файл | Что |
|---|---|
| `config.json` | Телефон, город, лимиты |
| `token.json` | Токен |
| `taste.json` | Память вкусов |
| `state.json` | Состояние напоминаний |

Переопределить: `export GROWFOOD_HOME=/path/to/dir`

```json
{
  "phone": "+7 (977) 123-45-67",
  "cityId": 2,
  "brandId_H": "lY",
  "max_extra_rub": 0,
  "lock_days": 3
}
```

| Поле | По умолчанию | Что |
|---|---|---|
| `cityId` | `2` | Москва |
| `brandId_H` | `"lY"` | Бренд Grow Food |
| `max_extra_rub` | `0` | Макс. доплата без `--allow-extra` (₽) |
| `lock_days` | `3` | За сколько дней закрывается день |

---

## Ежедневные напоминания

Пример через Hermes cron (19:30):

```bash
hermes cron create "30 19 * * *" --skill growfood --deliver telegram --prompt \
"Сценарий 1 навыка growfood (напоминание). Выполни pending. Если notify=false — ответь ровно [SILENT]. Иначе suggest --save, пришли замены, затем mark-reviewed."
```

Или просто напиши агенту: *«каждый день в 19:30 делай сценарий напоминания из навыка growfood»*.

---

## Безопасность

> [!WARNING]
> Токен даёт полный доступ к аккаунту Grow Food. Храни его в безопасности.

- Файлы: `600`, директория: `700`
- Скрипт **никогда** не вызывает оплату (`payments/charge-token`)
- **Никогда** не обходит капчу
- Не меняет подписку, доставку, адрес
- Не передавай токен через аргументы CLI — используй `set-token` через stdin

---

## API

Навык работает через мобильный API Grow Food (`mobile-apps/v2_32`), перехваченный из iOS‑приложения.

Flow: `orders/active` → `menu/customization` → `orders/customization/add` → `accept`

Подробнее: [docs/api-notes.md](docs/api-notes.md)

---

## Contributing

PR и issues приветствуются!

- 🐛 **Баги** — issue с логом
- 💡 **Фичи** — опиши use case
- 🔧 **PR** — форкни, сделай ветку, открой PR

---

## Лицензия

[MIT](LICENSE)
