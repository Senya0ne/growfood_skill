# 🍱 growfood_skill

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue?logo=python&logoColor=white)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen)]()

**Manage your Grow Food meal plan from a chat with an AI agent.**

🇷🇺 [Русская версия](README.md)

---

## What is this

An open-source skill for AI agents — [Hermes](https://github.com/NousResearch/hermes-agent), Claude Code, Antigravity, Codex, Cursor — that manages your [Grow Food](https://growfood.pro) meal delivery subscription via Telegram or chat.

It shows your menu, suggests meal swaps based on your taste preferences, replaces dishes with one command, remembers what you like and dislike, and collects ratings — all automatically, without opening the app.

> [!NOTE]
> [Grow Food](https://growfood.pro) is a prepared meal delivery service operating in Russia (Moscow, Saint Petersburg, and other cities).

---

## Features

- 📋 **Menu overview** — all active orders with taste icons (❤️ / ⛔ / ⚠️ / 🆕)
- 🤖 **Smart suggestions** — replacement picks based on taste history, no extra charge
- 🔄 **One-click swaps** — `replace`, `swap`, `autofix`
- 🧠 **Taste memory** — favorites, blocklist, disliked ingredients, free-form notes
- ⭐ **Meal ratings** — love / ok / bad / tired / allergy / portion
- 🔔 **Daily reminders** — cron job for the agent
- 💰 **Works on paid orders** — swaps on ✏️ days, shows surcharge/savings

---

## Quick Start

```bash
# 1. Clone into your agent's skills directory
git clone https://github.com/Senya0ne/growfood_skill.git \
  ~/.hermes/skills/growfood          # see table below for other agents

# 2. Get your token from the browser (see Token section)

# 3. Save the token
python3 ~/.hermes/skills/growfood/scripts/growfood.py set-token
# paste token → Enter

# 4. Done!
python3 ~/.hermes/skills/growfood/scripts/growfood.py review
```

Optional `gf` shortcut:

```bash
mkdir -p ~/.local/bin
cat > ~/.local/bin/gf <<'EOF'
#!/bin/sh
exec python3 "$HOME/.hermes/skills/growfood/scripts/growfood.py" "$@"
EOF
chmod +x ~/.local/bin/gf
# Now: gf review && gf suggest
```

---

## Installation by Platform

| Agent | Clone to |
|---|---|
| **Hermes** | `~/.hermes/skills/growfood/` |
| **Claude Code** | `~/.claude/skills/growfood/` |
| **Antigravity** | `~/.gemini/config/skills/growfood/` |
| **Cursor** | copy `.cursorrules` to project root |
| **Codex** | copy `AGENTS.md` to project root |

Platform adapters are in [`adapters/`](adapters/install.md).

> [!TIP]
> Works on Linux, macOS, and Raspberry Pi. Python 3.9+ standard library only — no pip install needed.

---

## Token

Grow Food protects SMS login with a captcha, so the agent can't log in automatically. You need to grab the token from your browser session:

1. Open [lk.growfood.pro](https://lk.growfood.pro) and log in normally (solve the captcha yourself).
2. **F12** → **Network** tab → reload page → click any request to `admin.growfood.pro`.
3. In **Request Headers**, copy the `x-client-token` value.
4. Run:
   ```bash
   gf set-token     # paste token → Enter
   ```

> [!IMPORTANT]
> - **Don't click "Log out"** in the web dashboard — it may revoke the token. Just close the tab.
> - When the token expires, the agent will tell you — repeat steps 1–4.

---

## Commands

| Command | Description |
|---|---|
| `review` | Show menu: ❤️ liked ⛔ blocked ⚠️ bad ingredient 🆕 unrated 🔁 swapped ⏳ pending |
| `suggest [--save]` | Suggest replacements (free only) |
| `apply-saved [nums…]` | Apply saved suggestions: all or `1 3` |
| `alternatives --date D --meal N` | Show alternatives for a specific slot |
| `replace --date D --meal N --pack ID` | Replace one dish |
| `swap D:N:ID [D:N:ID …]` | Multiple swaps at once |
| `revert --date D --meal N` | Revert to default dish |
| `autofix` | Apply all suggestions (free only) |
| `pending` | Reminder data: new ✏️ days, `closing_soon`, suggestions |
| `mark-reviewed` | Mark current ✏️ days as shown |
| `fav add\|rm ID` | Favorites |
| `stop add\|rm ID` | Blocklist |
| `taste show` | Show taste memory |
| `taste ingredient "stem"` | Add disliked ingredient stem |
| `taste note "text"` | Free-form taste note |
| `rate-tasks` | Dishes waiting for rating |
| `rate TASK love\|ok\|bad\|tired\|allergy\|portion` | Rate a dish |
| `orders` / `accept` / `drop` | Orders / confirm draft / drop unconfirmed |
| `login --phone` / `--code` | SMS login (if no captcha) |
| `set-token` | Save browser token (stdin) |

All swap commands support `--dry-run` and `--allow-extra`.

---

## How Scoring Works

| Factor | Score |
|---|---|
| ❤️ Liked (favorite) | **+3** |
| 🆕 Unrated | **0** |
| ⚠️ Disliked ingredient | **−6** |
| ⛔ Blocklisted | **−10** |
| 🔁 Repeated dish | **−1.5** per occurrence |
| 🍽️ Calorie proximity | bonus |
| 💰 Extra charge | penalty |

A swap is suggested when the current dish is ⚠️/⛔ and there's something better, or there's a ❤️ alternative with ≥ 3 point gain.

---

## Configuration

Files stored in `~/.config/growfood/` (permissions `600`):

| File | Purpose |
|---|---|
| `config.json` | Phone, city, limits |
| `token.json` | Auth token |
| `taste.json` | Taste memory |
| `state.json` | Reminder state |

Override directory: `export GROWFOOD_HOME=/path/to/dir`

| Field | Default | Meaning |
|---|---|---|
| `cityId` | `2` | City ID (2 = Moscow) |
| `brandId_H` | `"lY"` | Grow Food brand ID |
| `max_extra_rub` | `0` | Max extra charge per swap without `--allow-extra` (₽) |
| `lock_days` | `3` | Days before delivery when customization closes |

---

## Daily Reminders

Example Hermes cron job (daily at 19:30):

```bash
hermes cron create "30 19 * * *" --skill growfood --deliver telegram --prompt \
"Run growfood reminder scenario. Execute pending. If notify=false reply [SILENT]. Otherwise suggest --save, send numbered suggestions, then mark-reviewed."
```

Or just tell the agent: *"every day at 19:30, run the growfood reminder scenario"*.

---

## Security

> [!WARNING]
> The token grants full access to your Grow Food account. Keep it safe.

- Files created with `600` permissions, directory with `700`
- The script **never** calls payment endpoints (`payments/charge-token`)
- **Never** attempts to bypass captcha
- Does not modify subscription, delivery schedule, or address
- Don't pass the token as a CLI argument — use `set-token` via stdin

---

## API

The skill uses Grow Food's mobile API (`mobile-apps/v2_32`), reverse-engineered from the iOS app.

Flow: `orders/active` → `menu/customization` → `orders/customization/add` (full swap state) → `accept`

Details: [docs/api-notes.md](docs/api-notes.md)

---

## Contributing

PRs and issues are welcome!

- 🐛 **Bugs** — open an issue with the error log
- 💡 **Features** — describe the use case in an issue
- 🔧 **PRs** — fork, branch, pull request

---

## License

[MIT](LICENSE)
