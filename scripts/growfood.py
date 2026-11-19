#!/usr/bin/env python3
"""CLI client for Grow Food meal-delivery service (agent + manual use).

Designed for the Hermes agent, but works standalone from the command line.
Uses only the Python 3.9+ standard library — no external dependencies.

State files (permissions 600) in ~/.config/growfood/:
  config.json  — phone, city, brand, limits
  token.json   — client_token obtained via SMS login
  taste.json   — taste memory (likes / dislikes / ingredients / notes)
  state.json   — which days have been shown to the user (for reminders)

Wire protocol (reverse-engineered from Grow Food iOS app 11.5.1, Nov 2026):
  GET  mobile-apps/v2_32/orders/active                      — orders (6 days each)
  GET  mobile-apps/v2_32/menu/customization?orderId_H=…     — order menu: days[].items[]
       with mealNumber, major (default dish), secondary (slot alternatives), prices;
       customization[] — all swaps for the order (major_config = accepted, draft_* = draft).
  POST mobile-apps/v2_32/orders/customization/add            — FULL swap state for the order
       {orderId_H, customPacks:[{remove, packId, mealDate, mealNumber, isFreeCustom, isAutoCustom}]}
       The app always sends every swap (including already accepted ones) + new ones.
  POST mobile-apps/v2_32/orders/customization/accept {orderId_H} — confirm draft.
  POST mobile-apps/v2_32/orders/customization/drop   {orderId_H} — discard unconfirmed draft.

  Unpaid order: add → accept recalculates the amount that auto-payment will charge.
  Paid order: days with customizable=true CAN be changed; the price diff (draft.price)
       is a surcharge (order debt) or reduction. By default the script only makes
       swaps that do not incur a surcharge.
  The script NEVER calls payments/charge-token.
"""

from __future__ import annotations

__version__ = "1.0.0"

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

API = "https://admin.growfood.pro/api"
MOBILE = "mobile-apps/v2_32"
CONFIG_DIR = Path(os.environ.get("GROWFOOD_HOME", Path.home() / ".config" / "growfood"))
CONFIG_FILE = CONFIG_DIR / "config.json"
TOKEN_FILE = CONFIG_DIR / "token.json"
TASTE_FILE = CONFIG_DIR / "taste.json"
STATE_FILE = CONFIG_DIR / "state.json"

DEFAULT_CONFIG = {
    "phone": None,
    "cityId": 2,          # 2 = Moscow (Москва)
    "brandId_H": "lY",    # "lY" = Grow Food brand identifier
    "max_extra_rub": 0,       # доплата за одну замену, допустимая без --allow-extra
    "lock_days": 3,           # за сколько дней до даты питания день закрывается (наблюдение)
}

# id причин из storage.growfood.pro/s1/gf_data/feedback_data.json
RATE_REASONS = {
    "love": [201],     # Очень понравилось
    "ok": [4],         # Нормально
    "bad": [5],        # Невкусно (ветка «Что-то не так»)
    "tired": [136],    # Устал
    "allergy": [172],  # Не употребляю это / аллергия
    "portion": [578],  # Размер порций
}


class GFError(Exception):
    pass


def today() -> dt.date:
    return dt.date.fromisoformat(os.environ["GROWFOOD_TODAY"]) if os.environ.get("GROWFOOD_TODAY") else dt.date.today()


# ----------------------------------------------------------------- storage

def _read(path: Path, default):
    """Read a JSON file, returning *default* if it does not exist."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def _write(path: Path, data) -> None:
    """Atomically write JSON *data* to *path* with restrictive permissions."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def load_config() -> dict:
    """Load user config, filling missing keys from DEFAULT_CONFIG."""
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_read(CONFIG_FILE, {}))
    return cfg


def load_taste() -> dict:
    """Load local taste memory (likes, dislikes, unwanted ingredients, notes)."""
    t = _read(TASTE_FILE, {})
    t.setdefault("likes", {})        # packId -> name
    t.setdefault("dislikes", {})     # packId -> name
    t.setdefault("ingredients", [])  # нелюбимые ингредиенты (корни слов, нижний регистр)
    t.setdefault("notes", [])        # свободные заметки
    return t


# -------------------------------------------------------------------- http

def call(method: str, path: str, body=None, auth=True, params=None):
    """Make an authenticated API request and return the parsed JSON response."""
    url = f"{API}/{path}"
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://lk.growfood.pro",
        "Referer": "https://lk.growfood.pro/",
        "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "X-Application-Platform": "personal_cabinet",
    }
    if auth:
        tok = _read(TOKEN_FILE, {}).get("client_token")
        if not tok:
            raise GFError("Нет токена. Войди в Grow Food сам и выполни: growfood.py set-token")
        headers["X-Client-Token"] = tok
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    last = None
    for _attempt in range(2):  # один повтор на сетевой/5xx сбой
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                payload = json.loads(r.read().decode("utf-8") or "{}")
            break
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:500]
            if "enable_captcha" in text:
                raise GFError("Grow Food требует капчу для входа по SMS — автоматически войти нельзя. "
                              "Войди сам в браузере/приложении и передай токен: gf set-token") from None
            if e.code in (401, 403) and auth:
                raise GFError(f"Токен недействителен ({e.code}). Нужен новый токен: войди в Grow Food сам и выполни set-token") from None
            if e.code in (401, 403, 429):
                raise GFError(f"Grow Food отклонил вход ({e.code}): {text[:200]}. Используй gf set-token с токеном из браузера") from None
            last = GFError(f"HTTP {e.code} {path}: {text}")
            if e.code < 500:
                raise last from None
        except urllib.error.URLError as e:
            last = GFError(f"Сеть недоступна: {e.reason}")
    else:
        raise last
    if isinstance(payload, dict) and payload.get("success") is False:
        raise GFError(f"API вернул ошибку {path}: {json.dumps(payload, ensure_ascii=False)[:500]}")
    return payload


# -------------------------------------------------------------------- auth

def norm_phone(s: str) -> str:
    """Любой ввод → формат сайта «+7 (977) 123-45-67»."""
    d = re.sub(r"\D", "", s or "")
    if len(d) == 11 and d[0] in "78":
        d = d[1:]
    if len(d) != 10:
        raise GFError(f"Не похоже на российский номер: «{s}». Пример: +79771234567")
    return f"+7 ({d[:3]}) {d[3:6]}-{d[6:8]}-{d[8:]}"


def cmd_login(a, cfg):
    """Send an SMS code or verify a received code to log in."""
    if a.phone:
        phone = norm_phone(a.phone)
        cfg["phone"] = phone
        _write(CONFIG_FILE, {k: v for k, v in cfg.items()})
        r = call("POST", "personal-cabinet/v2_0/authentication/send-sms", auth=False, body={
            "analyticalData": {"cookie": {}, "utm": {}, "referrer": None, "host": "growfood.pro"},
            "client": {
                "phone": phone,
                "cityId": cfg["cityId"],
                "agree_personal_data_growfood": True,
            },
            "brandId_H": cfg["brandId_H"],
        })
        wait = r.get("resendAfter")
        return {"ok": True, "message": "SMS отправлено. Дальше: gf login --code XXXX"
                + (f" (повторно можно через {wait} с)" if wait else "")}
    if a.code:
        if not cfg.get("phone"):
            raise GFError("Сначала: gf login --phone +7...")
        r = call("POST", "personal-cabinet/v2_0/authentication/login", auth=False,
                 body={"phone": cfg["phone"], "password": re.sub(r"\D", "", a.code)})
        tok = r.get("client_token")
        if not tok:
            raise GFError("В ответе нет client_token — формат API мог измениться")
        _write(TOKEN_FILE, {"client_token": tok})
        name = (r.get("profile") or {}).get("firstName") or ""
        return {"ok": True, "message": f"Вход выполнен {name}".strip()}
    raise GFError("Укажи --phone или --code")


def cmd_whoami(a, cfg):
    """Return the current user's profile info (name, subscription status)."""
    tok = _read(TOKEN_FILE, {}).get("client_token")
    r = call("POST", "personal-cabinet/v1/authentication/check-token",
             body={"client_token": tok, "brandId_H": cfg["brandId_H"]})
    p = r.get("profile") or {}
    return {"ok": True, "name": p.get("first_name"), "has_active_subscription": p.get("has_active_subscription")}


def cmd_set_token(a, cfg):
    """Save a client token from a browser/app session. Prefers stdin to avoid shell history."""
    if a.token:
        tok = a.token.strip()
    else:
        tok = (sys.stdin.readline() if not sys.stdin.isatty() else input("Вставь X-Client-Token: ")).strip()
    if not re.fullmatch(r"[A-Za-z0-9]{40,512}", tok):
        raise GFError("Не похоже на токен (ожидаю строку из букв/цифр ~128 символов)")
    old = _read(TOKEN_FILE, None)
    _write(TOKEN_FILE, {"client_token": tok})
    try:
        r = call("GET", f"{MOBILE}/orders/active")
    except GFError:
        if old:
            _write(TOKEN_FILE, old)
        else:
            TOKEN_FILE.unlink(missing_ok=True)
        raise
    return {"ok": True, "message": f"Токен сохранён, активных заказов: {len(r.get('orders', []))}"}


# ------------------------------------------------------------------ orders

def get_orders(cfg, order_id=None) -> list[dict]:
    """Fetch active (non-completed) orders, optionally filtered by *order_id*."""
    r = call("GET", f"{MOBILE}/orders/active")
    orders = [o for o in r.get("orders", []) if (o.get("status") or {}).get("name") != "completed"]
    if order_id:
        orders = [o for o in orders if o["id_H"] == order_id]
        if not orders:
            raise GFError(f"Заказ {order_id} не найден среди активных")
    return orders


def is_paid(o: dict) -> bool:
    return (o.get("paidSum") or 0) > 0 or (o.get("status") or {}).get("name") == "formed"


def paid_delta(o: dict):
    """For a paid order, return the surcharge (+) or refund (−) implied by the draft."""
    draft = o.get("draft") or {}
    if not (draft.get("draft_accept") or {}).get("show"):
        return None
    if draft.get("price") is not None and o.get("paidSum"):
        return draft["price"] - o["paidSum"]
    return (draft.get("draft_accept") or {}).get("price")


def order_summary(o: dict) -> dict:
    return {
        "orderId_H": o["id_H"],
        "title": o.get("title"),
        "status": (o.get("status") or {}).get("name"),
        "paid": is_paid(o),
        "paidSum": o.get("paidSum"),
        "price": o.get("debPrice") if not is_paid(o) else o.get("paidSum"),
        "draftDelta": paid_delta(o) if is_paid(o) else None,
        "customizeUntil": o.get("confirmCustomizationUntil"),
        "paymentDate": o.get("clientSubscriptionPaymentDate") if not is_paid(o) else None,
    }


def cmd_orders(a, cfg):
    """List active orders with payment and customization status."""
    return [order_summary(o) for o in get_orders(cfg, a.order)]


# -------------------------------------------------------------------- menu

def get_custom(order_id: str) -> dict:
    """Fetch the full customization data (menu + swaps) for an order."""
    r = call("GET", f"{MOBILE}/menu/customization", params={"orderId_H": order_id})
    return r.get("data") or r


def pack_info(packs: dict, pid, price=None) -> dict:
    p = packs.get(str(pid)) or {}
    ingredients = "; ".join(d.get("ingredients") or "" for d in p.get("dishes", []))
    return {
        "packId": int(pid),
        "name": p.get("name") or f"#{pid}",
        "kcal": p.get("calories"), "p": p.get("proteins"), "f": p.get("fats"), "c": p.get("carbs"),
        "price": price if price is not None else p.get("price"),
        "ingredients": ingredients,
        "isFavorite": p.get("isFavorite"), "isRestricted": p.get("isRestricted"),
    }


def flags_for(info: dict, taste: dict) -> list[str]:
    """Return taste flags for a dish: 'like', 'dislike', 'ingredient:…', or 'new'."""
    f = []
    pid = str(info["packId"])
    if info.get("isFavorite") or pid in taste["likes"] or info["name"] in taste["likes"].values():
        f.append("like")
    if info.get("isRestricted") or pid in taste["dislikes"] or info["name"] in taste["dislikes"].values():
        f.append("dislike")
    low = (info["name"] + " " + info["ingredients"]).lower()
    # «рис» ловит «рис», «риса», «рисовая», но не «кристалл» / «барбарис»
    hits = [i for i in taste["ingredients"] if re.search(rf"(?<!\w){re.escape(i)}\w{{0,4}}(?!\w)", low)]
    if hits:
        f.append("ingredient:" + ",".join(hits))
    if not f:
        f.append("new")
    return f


def build_days(o: dict, data: dict, taste: dict) -> list[dict]:
    """Build a list of day dicts with merged meals, swaps, and taste flags."""
    packs = data.get("packs", {})
    days = []
    for dlv in data.get("deliveries", []):
        cust: dict[tuple, list] = {}
        for c in dlv.get("customization", []):
            cust.setdefault((c["mealDate"], int(c["mealNumber"])), []).append(c)
        for day in dlv.get("days", []):
            meals = []
            for it in day.get("items", []):
                n = int(it.get("mealNumber", it.get("position", 0)))
                prices = {int(k): v for k, v in (it.get("prices") or {}).items()}
                major = [int(x) for x in it.get("major") or []]
                current = list(major)
                changes = cust.get((day["mealDate"], n), [])
                for c in changes:
                    pid = int(c["packId"])
                    current = [x for x in current if x != pid] if c.get("customizeType") == "remove" else current + [pid]
                entries = []
                for pid in current:
                    info = pack_info(packs, pid, prices.get(pid))
                    info["flags"] = flags_for(info, taste)
                    entries.append(info)
                meals.append({
                    "meal": n, "title": it.get("title"),
                    "optional": not major,       # снеки/напитки — платные добавки
                    "customizable": bool(it.get("customizable")) and bool(day.get("customizable")),
                    "changed": bool(changes),
                    "pending": any("unaccepted" in str(c.get("configType")) for c in changes),
                    "major": major, "packs": entries,
                    "secondary": [int(x) for x in it.get("secondary", [])], "prices": prices,
                })
            days.append({"date": day["mealDate"], "orderId_H": o["id_H"], "paid": is_paid(o),
                         "customizable": bool(day.get("customizable")), "meals": meals})
    return days


def load_all(cfg, order_id=None, include_past=False):
    """→ (orders, datas{orderId: data}, days[]) по всем активным заказам."""
    taste = load_taste()
    orders = get_orders(cfg, order_id)
    datas, days = {}, {}
    t0 = today().isoformat()
    for o in orders:
        data = get_custom(o["id_H"])
        datas[o["id_H"]] = data
        for d in build_days(o, data, taste):
            if not include_past and d["date"] < t0:
                continue
            prev = days.get(d["date"])
            if prev is None or (d["customizable"] and not prev["customizable"]):
                days[d["date"]] = d
    return orders, datas, [days[k] for k in sorted(days)]


ICON = {"like": "❤️", "dislike": "⛔", "new": "🆕"}


def _icons(p):
    return " ".join(ICON.get(f, "⚠️ " + f.split(":", 1)[-1]) for f in p["flags"])


def render_days(days: list[dict], show_optional=False) -> str:
    lines = []
    for d in days:
        lock = "✏️" if d["customizable"] else "🔒"
        lines.append(f"\n📅 {d['date']} {lock}")
        for m in d["meals"]:
            if m["optional"] and not m["packs"] and not show_optional:
                continue
            for p in m["packs"] or [{"name": "—", "packId": "", "flags": [], "kcal": None}]:
                kcal = f" · {p['kcal']} ккал" if p.get("kcal") else ""
                mark = " 🔁" if m["changed"] else ""
                mark += " ⏳" if m["pending"] else ""
                lines.append(f"  [{m['meal']}] {m['title']}: {p['name']} (#{p['packId']}){kcal} {_icons(p)}{mark}")
    return "\n".join(lines).strip()


def cmd_review(a, cfg):
    """Show the current meal plan for active orders (human-readable or JSON)."""
    orders, _datas, days = load_all(cfg, a.order)
    if not a.all:
        locked = [d["date"] for d in days if not d["customizable"]]
        days = [d for d in days if d["customizable"]] or days
    else:
        locked = []
    if a.json:
        for d in days:
            for m in d["meals"]:
                m.pop("secondary"); m.pop("prices")
        return {"orders": [order_summary(o) for o in orders], "days": days, "locked_hidden": locked}
    heads = []
    for o in orders:
        s = order_summary(o)
        pay = f"оплачен {s['paidSum']} ₽" if s["paid"] else f"к оплате {s['price']} ₽ ({(s['paymentDate'] or '')[:10]})"
        delta = f", черновик {s['draftDelta']:+} ₽" if s.get("draftDelta") else ""
        heads.append(f"🧾 {s['orderId_H']} · {s['title']} · {pay}{delta}")
    if locked:
        heads.append(f"🔒 скрыто {len(locked)} дн. ({locked[0][5:]}–{locked[-1][5:]}), менять уже нельзя; --all — показать")
    return "\n".join(heads) + "\n" + render_days(days, a.optional)


def find_slot(days, date, meal):
    for d in days:
        if d["date"] == date:
            for m in d["meals"]:
                if m["meal"] == meal:
                    return d, m
    raise GFError(f"Нет слота {date} / приём {meal} (или дата в прошлом)")


def alternatives_for(slot: dict, data: dict, taste: dict, show_all=False) -> list[dict]:
    """Return available alternative dishes for a meal slot, filtered by taste prefs."""
    packs = data.get("packs", {})
    prices = slot["prices"]
    cur = {p["packId"] for p in slot["packs"]}
    base = prices.get(slot["major"][0], 0) if slot["major"] else 0
    res = {}
    for pid in slot["secondary"] + slot["major"]:
        if pid in cur or pid in res:
            continue
        info = pack_info(packs, pid, prices.get(pid))
        info["flags"] = flags_for(info, taste)
        if not show_all and ("dislike" in info["flags"] or any(f.startswith("ingredient:") for f in info["flags"])):
            continue
        info["extra"] = (info["price"] or 0) - (base or 0)
        info["isDefault"] = pid in slot["major"]
        res[pid] = info
    return list(res.values())


def cmd_alternatives(a, cfg):
    """List alternative dishes for a specific date and meal slot."""
    _orders, datas, days = load_all(cfg, a.order)
    day, slot = find_slot(days, a.date, a.meal)
    taste = load_taste()
    alts = alternatives_for(slot, datas[day["orderId_H"]], taste, a.all)
    base_kcal = next((p["kcal"] for p in slot["packs"] if p.get("kcal")), 0) or 0
    alts.sort(key=lambda x: (0 if "like" in x["flags"] else 1, max(x["extra"], 0), abs((x["kcal"] or 0) - base_kcal)))
    alts = alts[: a.limit]
    if a.json:
        for x in alts:
            x.pop("ingredients", None)
        return {"date": a.date, "meal": a.meal, "title": slot["title"], "customizable": slot["customizable"],
                "paidOrder": day["paid"], "current": [p["name"] for p in slot["packs"]], "alternatives": alts}
    head = (f"{a.date} [{a.meal}] {slot['title']} сейчас: " + ", ".join(p["name"] for p in slot["packs"])
            + ("" if slot["customizable"] else "  🔒 менять нельзя"))
    body = [f"  #{r['packId']} {r['name']} · {r['kcal']} ккал · {r['extra']:+} ₽ {'❤️' if 'like' in r['flags'] else ''}"
            f"{' (стандартное)' if r['isDefault'] else ''}" for r in alts]
    return head + "\n" + "\n".join(body)


# ---------------------------------------------------------------- suggest

SCORE = {"like": 3.0, "new": 0.0, "dislike": -10.0}


def score(info: dict, uses: Counter) -> float:
    """Score a dish based on taste flags and repetition count (higher = better)."""
    s = sum(SCORE.get(f, -6.0) for f in info["flags"])  # ingredient:* → −6
    s -= 1.5 * max(uses[info["name"]] - 1, 0)           # не повторять одно и то же
    return s


def make_suggestions(cfg, days, datas, budget=0, only_dates=None):
    """Generate swap suggestions: replace disliked/unwanted dishes with better alternatives."""
    taste = load_taste()
    uses = Counter(p["name"] for d in days for m in d["meals"] for p in m["packs"])
    out = []
    for d in days:
        if not d["customizable"] or (only_dates and d["date"] not in only_dates):
            continue
        for m in d["meals"]:
            if not m["customizable"] or m["optional"] or len(m["packs"]) != 1:
                continue
            cur = m["packs"][0]
            if m["changed"] and "dislike" not in cur["flags"]:
                continue  # это выбрал сам пользователь — не перебиваем
            cur_s = score(cur, uses)
            limit = budget if not d["paid"] else min(budget, 0)
            alts = [x for x in alternatives_for(m, datas[d["orderId_H"]], taste) if x["extra"] <= limit]
            if not alts:
                continue
            for x in alts:
                x["_s"] = (score(x, Counter({**uses, x["name"]: uses[x["name"]] + 1}))
                           - 0.002 * max(x["extra"], 0)
                           - 0.003 * abs((x["kcal"] or 0) - (cur["kcal"] or 0)))  # близко по калориям
            best = max(alts, key=lambda x: x["_s"])
            bad = "dislike" in cur["flags"] or any(f.startswith("ingredient:") for f in cur["flags"])
            gain = best["_s"] - cur_s
            if (bad and gain > 0) or gain >= 3:
                reason = ("не любишь: " + ", ".join(f.split(":", 1)[-1] for f in cur["flags"] if f != "new")) if bad \
                    else "есть любимое"
                out.append({"date": d["date"], "meal": m["meal"], "title": m["title"],
                            "from": cur["name"], "fromId": cur["packId"],
                            "to": best["name"], "packId": best["packId"], "extra": best["extra"],
                            "reason": reason})
                uses[best["name"]] += 1
                uses[cur["name"]] -= 1
    return out


def cmd_suggest(a, cfg):
    """Suggest meal swaps based on taste preferences and show unrated dishes."""
    _orders, datas, days = load_all(cfg, a.order)
    sug = make_suggestions(cfg, days, datas, budget=cfg.get("max_extra_rub", 0))
    unknown = sorted({(p["packId"], p["name"]) for d in days if d["customizable"] for m in d["meals"]
                      for p in m["packs"] if p["flags"] == ["new"] and not m["optional"]})
    for i, s in enumerate(sug, 1):
        s["n"] = i
    if a.save:  # чтобы ответ пользователя «да» / «1,3» можно было применить в другой сессии
        st = _read(STATE_FILE, {"reviewed": []})
        st["proposal"] = {"created": dt.datetime.now().isoformat(timespec="minutes"), "items": sug}
        _write(STATE_FILE, st)
    if a.json:
        return {"suggestions": sug, "unknown": [{"packId": i, "name": n} for i, n in unknown]}
    lines = [f"  {s['n']}) {s['date']} {s['title']}: {s['from']} → {s['to']} ({s['extra']:+} ₽) — {s['reason']}"
             for s in sug] or ["  замен не предлагаю"]
    lines.append(f"Неоценённых блюд: {len(unknown)}")
    return "\n".join(lines)


def cmd_apply_saved(a, cfg):
    """Apply previously saved suggestions (all or selected by number)."""
    st = _read(STATE_FILE, {})
    prop = st.get("proposal") or {}
    items = prop.get("items") or []
    if not items:
        raise GFError("Нет сохранённых предложений (сначала suggest --save)")
    chosen = [s for s in items if not a.numbers or s["n"] in a.numbers]
    if not chosen:
        raise GFError(f"Нет пунктов {a.numbers}; есть 1..{len(items)}")
    res = apply_swaps(cfg, [(s["date"], s["meal"], s["packId"]) for s in chosen], False, a.no_accept, a.dry_run)
    if not a.dry_run:
        st["proposal"] = {}
        _write(STATE_FILE, st)
    return res


# ------------------------------------------------------------------- swaps

def all_custom_packs(data: dict) -> list[dict]:
    """Collect all existing swaps (accepted + draft) in customization/add body format."""
    out = []
    for dlv in data.get("deliveries", []):
        for c in dlv.get("customization", []):
            out.append({
                "remove": c.get("customizeType") == "remove",
                "packId": int(c["packId"]),
                "mealDate": c["mealDate"],
                "mealNumber": int(c["mealNumber"]),
                "isFreeCustom": bool(c.get("isFreeCustom")),
                "isAutoCustom": bool(c.get("isAutoCustom")),
            })
    return out


def apply_swaps(cfg, swaps: list[tuple], allow_extra=False, no_accept=False, dry_run=False, order_id=None):
    """Apply meal swaps: validate, build payload, POST to API, and optionally accept."""
    orders, datas, days = load_all(cfg, order_id)
    by_order: dict[str, list] = {}
    results = []
    for date, meal, pack in swaps:
        day, slot = find_slot(days, date, meal)
        if not slot["customizable"]:
            raise GFError(f"{date} [{meal}] {slot['title']}: этот день/приём менять уже нельзя 🔒")
        if not slot["major"]:
            raise GFError(f"{date} [{meal}] {slot['title']}: необязательный приём (снеки/напиток) — добавление платное, не делаю")
        new = pack if pack is not None else slot["major"][0]
        if new not in set(slot["secondary"]) | set(slot["major"]):
            raise GFError(f"Блюдо #{new} недоступно для {date} [{meal}]. Смотри alternatives.")
        extra = (slot["prices"].get(new) or 0) - (slot["prices"].get(slot["major"][0]) or 0)
        name = (datas[day["orderId_H"]].get("packs", {}).get(str(new)) or {}).get("name", f"#{new}")
        r = {"date": date, "meal": meal, "title": slot["title"], "orderId_H": day["orderId_H"], "paidOrder": day["paid"],
             "from": ", ".join(p["name"] for p in slot["packs"]), "to": name, "packId": new,
             "extra": extra, "isRevert": new in slot["major"]}
        if pack is not None and extra > cfg.get("max_extra_rub", 0) and not allow_extra:
            raise GFError(f"{date} [{meal}] {name}: доплата {extra:+} ₽ (до скидки). Нужна явная просьба пользователя → --allow-extra")
        results.append(r)
        by_order.setdefault(day["orderId_H"], []).append((date, meal, new, slot["major"]))

    reports = []
    for oid, items in by_order.items():
        packs = all_custom_packs(datas[oid])
        for date, meal, new, major in items:
            packs = [p for p in packs if not (p["mealDate"] == date and p["mealNumber"] == meal)]
            if new not in major:
                packs += [{"remove": True, "packId": m, "mealDate": date, "mealNumber": meal,
                           "isFreeCustom": False, "isAutoCustom": False} for m in major]
                packs.append({"remove": False, "packId": new, "mealDate": date, "mealNumber": meal,
                              "isFreeCustom": False, "isAutoCustom": False})
        body = {"orderId_H": oid, "customPacks": packs}
        o = next(o for o in orders if o["id_H"] == oid)
        rep = {"orderId_H": oid, "paid": is_paid(o), "price_before": order_summary(o)["price"]}
        if dry_run:
            rep.update({"dry_run": True, "body": body})
            reports.append(rep)
            continue
        added = call("POST", f"{MOBILE}/orders/customization/add", body=body)
        ao = added.get("order") or {}
        delta = paid_delta(ao) if is_paid(o) else None
        rep["draft_total"] = (ao.get("draft") or {}).get("price")
        if is_paid(o) and delta and delta > 0 and not allow_extra:
            call("POST", f"{MOBILE}/orders/customization/drop", body={"orderId_H": oid})
            raise GFError(f"Заказ {oid} оплачен, а замены дают доплату {delta} ₽ — отменил черновик. Нужна --allow-extra и оплата в приложении.")
        if no_accept:
            rep["accepted"] = False
        else:
            acc = call("POST", f"{MOBILE}/orders/customization/accept", body={"orderId_H": oid})
            fo = acc.get("order") or ao
            rep.update({"accepted": True,
                        "price_after": fo.get("debPrice") if not is_paid(o) else None,
                        "paid_delta": delta if is_paid(o) else None})
        reports.append(rep)
    return {"ok": True, "swaps": results, "orders": reports}


def parse_swap(s: str) -> tuple:
    try:
        date, meal, pack = s.split(":")
        dt.date.fromisoformat(date)
        return date, int(meal), (None if pack in ("", "default", "0") else int(pack))
    except ValueError:
        raise GFError(f"Формат замены: ДАТА:ПРИЁМ:PACKID (например 2026-11-18:6:13444), получил «{s}»") from None


def cmd_swap(a, cfg):
    """Execute one or more swaps given as DATE:MEAL:PACKID arguments."""
    return apply_swaps(cfg, [parse_swap(s) for s in a.swaps], a.allow_extra, a.no_accept, a.dry_run, a.order)


def cmd_replace(a, cfg):
    """Replace a single meal slot with a specified dish."""
    return apply_swaps(cfg, [(a.date, a.meal, a.pack)], a.allow_extra, a.no_accept, a.dry_run, a.order)


def cmd_revert(a, cfg):
    """Revert a meal slot back to its default (standard) dish."""
    return apply_swaps(cfg, [(a.date, a.meal, None)], True, a.no_accept, a.dry_run, a.order)


def cmd_autofix(a, cfg):
    """Apply all free-of-charge suggestions from the suggest engine."""
    _orders, datas, days = load_all(cfg, a.order)
    sug = make_suggestions(cfg, days, datas, budget=0)
    if not sug:
        return {"ok": True, "swaps": [], "message": "Менять нечего"}
    return apply_swaps(cfg, [(s["date"], s["meal"], s["packId"]) for s in sug], False, a.no_accept, a.dry_run, a.order)


def cmd_accept(a, cfg):
    """Accept (confirm) all pending draft customizations."""
    out = []
    for o in get_orders(cfg, a.order):
        r = call("POST", f"{MOBILE}/orders/customization/accept", body={"orderId_H": o["id_H"]})
        out.append(order_summary(r.get("order") or o))
    return out


def cmd_drop(a, cfg):
    """Drop (discard) all unaccepted draft customizations."""
    out = []
    for o in get_orders(cfg, a.order):
        call("POST", f"{MOBILE}/orders/customization/drop", body={"orderId_H": o["id_H"]})
        out.append(o["id_H"])
    return {"ok": True, "dropped_unaccepted_in": out}


# --------------------------------------------------------------- reminders

def cmd_pending(a, cfg):
    """Check if there is a reason to notify the user (new days, closing deadlines, suggestions)."""
    _orders, datas, days = load_all(cfg, a.order)
    st = _read(STATE_FILE, {"reviewed": []})
    t0 = today()
    reviewed = {d for d in st.get("reviewed", []) if d >= t0.isoformat()}
    editable = [d["date"] for d in days if d["customizable"]]
    new = [d for d in editable if d not in reviewed]
    lock = int(cfg.get("lock_days", 3))
    closing = [d for d in editable if (dt.date.fromisoformat(d) - t0).days <= lock + 1]
    closing_unreviewed = [d for d in closing if d in new]
    sug = make_suggestions(cfg, days, datas, budget=0, only_dates=set(new)) if new else []
    rate = []
    try:
        rate = cmd_rate_tasks(a, cfg)
    except GFError:
        pass
    notify = bool(new) and (len(new) >= 2 or bool(closing_unreviewed) or bool(sug))
    return {"notify": notify, "new_editable_days": new, "closing_soon": closing_unreviewed,
            "editable_days": editable, "suggestions": sug, "rate_tasks": len(rate)}


def cmd_mark_reviewed(a, cfg):
    """Mark dates as reviewed so pending no longer flags them."""
    st = _read(STATE_FILE, {"reviewed": []})
    dates = a.dates
    if not dates:
        _o, _d, days = load_all(cfg, a.order)
        dates = [d["date"] for d in days if d["customizable"]]
    t0 = today().isoformat()
    st["reviewed"] = sorted({d for d in st.get("reviewed", []) + dates if d >= t0})
    _write(STATE_FILE, st)
    return {"ok": True, "reviewed": st["reviewed"]}


# ------------------------------------------------------------ prefs (сервер)

def cmd_prefs(a, cfg):
    """Fetch server-side favorites and stoplist."""
    r = call("GET", "personal-cabinet/v1/prefs/")
    packs = r.get("packs") or {}
    if isinstance(packs, list):
        packs = {str(p.get("id")): p for p in packs}

    def name(pid):
        return (packs.get(str(pid)) or {}).get("name") or f"#{pid}"

    return {
        "favorites": [{"packId": x["packId"], "name": name(x["packId"])} for x in r.get("favorites", [])],
        "stoplist": [{"packId": x["packId"], "name": name(x["packId"])} for x in r.get("restrictions", [])],
    }


def _pref(pid: int, favorite: bool, add: bool):
    if add:
        r = call("POST", "personal-cabinet/v1/prefs/", body={"isFavorite": favorite, "action": "change", "packId": pid})
    else:
        r = call("POST", "personal-cabinet/v1/prefs/delete", body={"isFavorite": favorite, "action": "remove", "packId": pid})
    return (r.get("pack") or {}).get("name") or f"#{pid}"


def _remember(pid: int, name: str, like: bool | None):
    t = load_taste()
    for k in ("likes", "dislikes"):
        t[k].pop(str(pid), None)
    if like is True:
        t["likes"][str(pid)] = name
    elif like is False:
        t["dislikes"][str(pid)] = name
    _write(TASTE_FILE, t)


def cmd_fav(a, cfg):
    """Add or remove a dish from the favorites list (server + local taste memory)."""
    name = a.name or f"#{a.pack}"
    try:
        name = _pref(a.pack, True, a.action == "add")
        server = True
    except GFError as e:  # у некоторых паков canUseFavorites=false — помним локально
        server = str(e)
    _remember(a.pack, name, True if a.action == "add" else None)
    return {"ok": True, "favorite": a.action == "add", "packId": a.pack, "name": name, "server": server}


def cmd_stop(a, cfg):
    """Add or remove a dish from the stoplist (server + local taste memory)."""
    name = a.name or f"#{a.pack}"
    try:
        name = _pref(a.pack, False, a.action == "add")
        server = True
    except GFError as e:
        server = str(e)
    _remember(a.pack, name, False if a.action == "add" else None)
    return {"ok": True, "stoplist": a.action == "add", "packId": a.pack, "name": name, "server": server}


# ----------------------------------------------------------- taste (локально)

def cmd_taste(a, cfg):
    """Manage local taste memory: like/dislike dishes, block ingredients, add notes."""
    t = load_taste()
    if a.action == "show":
        return t
    if a.action == "ingredient":
        v = a.value.lower().strip()
        if v not in t["ingredients"]:
            t["ingredients"].append(v)
    elif a.action == "uningredient":
        t["ingredients"] = [i for i in t["ingredients"] if i != a.value.lower().strip()]
    elif a.action == "note":
        t["notes"].append(a.value)
    elif a.action in ("like", "dislike", "forget"):
        pid = str(int(a.value))
        for k in ("likes", "dislikes"):
            t[k].pop(pid, None)
        if a.action != "forget":
            t["likes" if a.action == "like" else "dislikes"][pid] = a.name or f"#{pid}"
    _write(TASTE_FILE, t)
    return {"ok": True, "taste": t}


# -------------------------------------------------------------------- rating

def cmd_rate_tasks(a, cfg):
    """List pending dish rating tasks from the server."""
    r = call("GET", "personal-cabinet/v1/rating-tasks/get")
    out = []
    for t in (r.get("tasks") or {}).get("dish") or []:
        dish = t.get("dish") or {}
        out.append({"taskId": t["id"], "date": t.get("date"), "meal": t.get("title"),
                    "packId": t.get("packId"), "name": dish.get("name") or (t.get("pack") or {}).get("name")})
    return out


def cmd_rate(a, cfg):
    """Submit a rating for a dish (love/ok/bad/tired/allergy/portion)."""
    reasons = RATE_REASONS[a.mark]
    call("POST", f"personal-cabinet/v1/rating-tasks/{a.task}",
         body={"task_id": a.task, "feedback_data": {"feedback_reason_ids": reasons, "comment": a.comment}})
    return {"ok": True, "taskId": a.task, "mark": a.mark}


# ---------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(prog="growfood.py", description="Grow Food CLI")
    ap.add_argument("--json", action="store_true", help="машиночитаемый вывод")
    ap.add_argument("--order", help="ограничиться одним заказом (orderId_H)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("login"); p.add_argument("--phone"); p.add_argument("--code"); p.set_defaults(fn=cmd_login)
    sp.add_parser("whoami").set_defaults(fn=cmd_whoami)
    p = sp.add_parser("set-token", help="сохранить X-Client-Token из своей сессии")
    p.add_argument("token", nargs="?", help="можно не указывать — тогда спросит"); p.set_defaults(fn=cmd_set_token)
    sp.add_parser("orders").set_defaults(fn=cmd_orders)
    sp.add_parser("order").set_defaults(fn=cmd_orders)
    for n in ("review", "menu"):
        p = sp.add_parser(n); p.add_argument("--optional", action="store_true", help="показывать снеки/напитки")
        p.add_argument("--all", action="store_true", help="показывать и закрытые 🔒 дни")
        p.set_defaults(fn=cmd_review)
    p = sp.add_parser("alternatives")
    p.add_argument("--date", required=True); p.add_argument("--meal", type=int, required=True)
    p.add_argument("--limit", type=int, default=12); p.add_argument("--all", action="store_true", help="не скрывать нелюбимое")
    p.set_defaults(fn=cmd_alternatives)
    p = sp.add_parser("suggest"); p.add_argument("--save", action="store_true", help="сохранить для apply-saved")
    p.set_defaults(fn=cmd_suggest)
    p = sp.add_parser("apply-saved", help="применить сохранённые предложения (все или номера)")
    p.add_argument("numbers", nargs="*", type=int); p.add_argument("--no-accept", action="store_true")
    p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_apply_saved)

    def swap_flags(p, extra=True):
        if extra:
            p.add_argument("--allow-extra", action="store_true", help="разрешить замену с доплатой")
        p.add_argument("--no-accept", action="store_true", help="оставить в черновике")
        p.add_argument("--dry-run", action="store_true")

    p = sp.add_parser("replace"); p.add_argument("--date", required=True); p.add_argument("--meal", type=int, required=True)
    p.add_argument("--pack", type=int, required=True); swap_flags(p); p.set_defaults(fn=cmd_replace)
    p = sp.add_parser("revert"); p.add_argument("--date", required=True); p.add_argument("--meal", type=int, required=True)
    swap_flags(p, extra=False); p.set_defaults(fn=cmd_revert)
    p = sp.add_parser("swap", help="несколько замен одним запросом: ДАТА:ПРИЁМ:PACKID …")
    p.add_argument("swaps", nargs="+"); swap_flags(p); p.set_defaults(fn=cmd_swap)
    p = sp.add_parser("autofix", help="применить suggest (только бесплатные)"); swap_flags(p, extra=False)
    p.set_defaults(fn=cmd_autofix)
    sp.add_parser("accept").set_defaults(fn=cmd_accept)
    sp.add_parser("drop").set_defaults(fn=cmd_drop)

    sp.add_parser("pending").set_defaults(fn=cmd_pending)
    p = sp.add_parser("mark-reviewed"); p.add_argument("dates", nargs="*"); p.set_defaults(fn=cmd_mark_reviewed)

    sp.add_parser("prefs").set_defaults(fn=cmd_prefs)
    for n, fn in (("fav", cmd_fav), ("stop", cmd_stop)):
        p = sp.add_parser(n); p.add_argument("action", choices=["add", "rm"]); p.add_argument("pack", type=int)
        p.add_argument("--name"); p.set_defaults(fn=fn)
    p = sp.add_parser("taste")
    p.add_argument("action", choices=["show", "like", "dislike", "forget", "ingredient", "uningredient", "note"])
    p.add_argument("value", nargs="?"); p.add_argument("--name"); p.set_defaults(fn=cmd_taste)
    sp.add_parser("rate-tasks").set_defaults(fn=cmd_rate_tasks)
    p = sp.add_parser("rate"); p.add_argument("task", type=int); p.add_argument("mark", choices=list(RATE_REASONS))
    p.add_argument("--comment"); p.set_defaults(fn=cmd_rate)

    a = ap.parse_args(argv)
    try:
        res = a.fn(a, load_config())
    except GFError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False) if a.json else f"Ошибка: {e}", file=sys.stderr)
        return 1
    if isinstance(res, str):
        print(res)
    else:
        print(json.dumps(res, ensure_ascii=False, indent=None if a.json else 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
