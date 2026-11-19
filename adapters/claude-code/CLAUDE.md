# Grow Food — помощник по меню (Claude Code)

Этот файл — адаптер SKILL.md для Claude Code. Полные инструкции: `SKILL.md` в корне репо.

## Настройка

```bash
GF="python3 $HOME/.claude/skills/growfood-skill/scripts/growfood.py"
$GF --json <команда>
```

Или через `GROWFOOD_HOME`:
```bash
export GROWFOOD_HOME="$HOME/.claude/skills/growfood-skill"
GF="python3 $GROWFOOD_HOME/scripts/growfood.py"
```

## Инструкции

Следуй инструкциям из `SKILL.md` в корне этого репозитория — сценарии, команды и правила.

Ключевое:
- Для разбора всегда `--json`; пользователю — короткий текст по-русски.
- Скрипт НИКОГДА не вызывает оплату, не обходит капчу.
- `--allow-extra` только с явного согласия пользователя в этом диалоге.
- Не трогай 🔒 дни, снеки, напитки, ручные замены (🔁).
