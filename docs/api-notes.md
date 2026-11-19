# Grow Food API — Reverse-Engineered Notes

> **Disclaimer**: These notes are based on observing network traffic from the Grow Food web cabinet (lk.growfood.pro) and iOS app. The API is undocumented and may change without notice. Use at your own risk.

Backend base URL: `https://admin.growfood.pro/api/...`

The web cabinet uses **two endpoint families** with the same auth token:
- `personal-cabinet/*` — web-specific endpoints
- `mobile-apps/v2_31/*` / `v2_32/*` — mobile app endpoints (also work with web tokens)

## Authentication

| Method | Path | Notes |
|---|---|---|
| POST | `personal-cabinet/v2_0/authentication/send-sms` | phone → SMS code |
| POST | `personal-cabinet/v2_0/authentication/login` | SMS code → token |
| POST | `personal-cabinet/v1/authentication/check-token` | validate token is alive |

**Auth header:** `X-Client-Token: <token>`

The mobile app login uses additional signing (`X-Request-Signature`, `X-Application-Token`), so the script authenticates via web endpoints and uses the same token for mobile API calls (the web cabinet does this too).

## Read Endpoints ✅

| Method | Path | Returns |
|---|---|---|
| GET | `mobile-apps/v2_31/customization-info/possible-dates` | delivery dates, `isCustomizable` flags |
| POST | `mobile-apps/v2_31/customization-info/picker-menu` | current menu + all alternatives |
| POST | `personal-cabinet/v1_1/menu/full` | full menu |
| GET | `personal-cabinet/v2_0/menu/meal-days` | meal days |
| GET | `personal-cabinet/v2_0/orders/active` | active orders |
| GET | `personal-cabinet/v1/client-subscriptions` | subscriptions |

### `picker-menu` Request/Response

Request:
```json
{
  "pricePlanId": 2139,
  "mealPlanId": 101,
  "targetCalories": 750,
  "targetMealsCount": 3,
  "plannedDeliveryDate": "2026-10-19",
  "daysCount": 6,
  "showDenormalizedCustomConfig": true,
  "filterIds": [],
  "meals": [0, 1, 2]
}
```

Key fields in response:
- `data.deliveries[].customization[]` — current selections: `{customizeType, mealDate, mealNumber, packId, reason, isFreeCustom}`
- `data.deliveries[].secondary[]` — available alternatives (~140+ packs)
- `data.deliveries[].prices[]` — pack prices (330–390 ₽), `config.packRemovePrice: 50`
- `data.deliveries[].days[].customizable` — whether the day can be modified
- `data.packs{packId → name, nutrition, dishes[].ingredients, isFavorite, isRestricted}`

## Write Endpoints — Preferences ✅

| Method | Path | Body |
|---|---|---|
| POST | `personal-cabinet/v1/prefs/` | `{"isFavorite": true/false, "action": "change", "packId": N}` — add to favorites / stop-list |
| POST | `personal-cabinet/v1/prefs/delete` | `{"isFavorite": …, "action": "remove", "packId": N}` |
| GET | `personal-cabinet/v1/prefs/` | returns `favorites[]`, `restrictions[]` |

## Write Endpoints — Ratings ✅

| Method | Path | Body |
|---|---|---|
| GET | `personal-cabinet/v1/rating-tasks/get` | dishes pending rating |
| POST | `personal-cabinet/v1/rating-tasks/{id}` | `{"task_id": N, "feedback_data": {"feedback_reason_ids": [4], "comment": null}}` |

## Meal Customization ✅

Discovered from iOS app traffic (v2_32):

| Method | Path | Purpose |
|---|---|---|
| GET | `mobile-apps/v2_32/menu/customization?orderId_H=…` | order menu: `days[].items[]` with `mealNumber`, `major` (default), `secondary` (alternatives for slot), `prices`; `customization[]` = draft replacements |
| POST | `mobile-apps/v2_32/orders/customization/add` | submit **full** replacement state |
| GET | `mobile-apps/v2_32/orders/active` | orders with `confirmCustomizationUntil`, `draft.price`, `draft.draft_accept.show` |

### `customization/add` Body

```json
{
  "orderId_H": "ABC123",
  "customPacks": [
    {"remove": true,  "packId": 13647, "mealDate": "2026-11-18", "mealNumber": 6, "isFreeCustom": false, "isAutoCustom": false},
    {"remove": false, "packId": 6670,  "mealDate": "2026-11-18", "mealNumber": 6, "isFreeCustom": false, "isAutoCustom": false}
  ]
}
```

**Important:** Each call replaces the **entire** customization list — the app sends all replacements every time.

### Pricing

- Price difference = `prices[new] − prices[major]` (before discount)
- Swapping to a cheaper pack reduces the total (e.g., −60 ₽)
- Swapping to a more expensive pack increases it (e.g., +325 ₽ after discount)

### Meal Slots

| Slot | Meal | Notes |
|---|---|---|
| 0 | Snacks | Paid add-on, no `major` — do not touch |
| 1 | Drink | Paid add-on, no `major` — do not touch |
| 2 | Breakfast | |
| 3 | Second breakfast | |
| 4 | Lunch | |
| 5 | Afternoon snack | |
| 6 | Dinner | |

## Confirmation & Payment Flow

| Step | Request | Effect |
|---|---|---|
| 1 | `POST orders/customization/add` | Creates `draft_unaccepted_config` |
| 2 | `POST orders/customization/accept {"orderId_H": "…"}` | → `draft_config`, updates `order.debPrice` |
| 3 | `POST payments/charge-token {"externalClientCardId_H": "…", "orderIds_HU": "…"}` | Payment via saved card (no 3-D Secure). Order → `formed`, customizations become `major_config` |

**The script performs steps 1 + 2 only. Payment is never triggered by the script** — it's handled by the subscription's auto-charge.

## Paid Orders

- After payment, `orders/active` returns `confirmCustomizationUntil: null`, but `menu/customization` still shows days with `customizable: true` (approx. meal date − 3 days) — **editing is allowed**.
- `add` sends **all** replacements including already-accepted ones (`major_config`) + new ones. After `add`, the order gets a new `confirmCustomizationUntil`.
- `order.draft.price` = new total; `draft.draft_accept.price` = delta. For upcharges, `orders/pay-details` shows the amount due.
- `POST orders/customization/drop {"orderId_H": "…"}` — discard unaccepted changes.
- Multiple active orders may exist (current + next) — check all non-`completed` orders.

## Open Questions

- Exact token TTL.
- Whether `config.canUseClientRestrictions` / `showFavoritesAndRestrictions` affects the menu generator on all plans.
