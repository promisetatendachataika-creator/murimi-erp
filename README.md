# Murimi Farm ERP

Farm management for mid-sized Zimbabwean farms: farms, fields, seasons and
crop plans, a team with roles, and money in USD, ZiG and rand. Part 3 of
Murimi, after Murimi Intelligent Hub and Murimi Market.

## Run it on your computer (Windows)

1. Double-click `start-windows.bat`. The first time, it sets up `.venv` and
   installs Django (about a minute).
2. Open http://127.0.0.1:8002 (port 8002, so the Hub on 8000 can run at the same time).
3. Click **Create account**, then **Set up my farm business**.

To create a staff login for the admin at http://127.0.0.1:8002/admin:

```
cd C:\murimi-erp
.venv\Scripts\python.exe manage.py createsuperuser
```

Run the automatic checks:

```
.venv\Scripts\python.exe manage.py test
```

## What's in here

| Folder | What it does |
| --- | --- |
| `config/` | Settings and the top-level address map |
| `murimi_common/` | Shared by every Murimi module: phone and username rules, provinces, crops, currencies, JSON helpers |
| `accounts/` | People: sign up and log in with WhatsApp number or username (same rules as the Hub) |
| `erp_core/` | Organisations (the paying farm business), team roles, farms, fields, seasons, crop plans, change history |
| `erp_finance/` | Currencies, exchange rates, converting between USD, ZiG and rand |
| `erp_trade/` | Customers and suppliers, sales invoices (draft → issued with the next number → paid), purchases (stock goes into the store), payments, harvests, VAT, the fiscal-device slot (`fiscal.py`) and the reports: crop cost and profit per hectare, profit and loss, VAT, who owes what. The money rules are in `trade.py` |
| `erp_labour/` | Workers, piece-work rates, the daily work register, advances, pay runs and payslips. The pay maths (PAYE, AIDS levy, NSSA) is in `payroll.py`; the tax tables and minimum wages are rows in the database, never numbers in the code |
| `erp_inventory/` | Stores and fuel tanks, items, machines, stock in batches (oldest used first), stock takes, undo, fuel use and warnings. The rules are in `stock.py` and `fuel.py` |
| `android/` | The Android app wrapper (Trusted Web Activity). GitHub builds the APK with `.github/workflows/android.yml` |
| `web/` | The web app (one page: `templates/web/app.html`, `static/web/app.js`, `static/web/app.css`) |

## Rules the code follows

- **One business never sees another's records.** Every address is
  `/api/erp/orgs/<organisation id>/...`; `erp_core/tenancy.py` checks you
  belong to that organisation first, and answers "not found" if you don't.
- **Roles** are in one table: `erp_core/permissions.py`.
- **Money is Decimal, never float**, rounded half-up to cents (`erp_finance/money.py`).
- **Exchange rates** are written as 1 USD = X ZiG, and today's rate is fetched online
  (`erp_finance/fetch.py`, also run by `start-windows.bat`). Your own rate beats
  Murimi's rate on the same day. Later stages store the rate used with each record.
- **Stock records are never deleted.** A mistake is undone with an opposite
  record. Each batch keeps what it cost in US$ and in ZiG on the day it arrived.
- **Every change is recorded** in the change history (`AuditEvent`), which
  can't be edited or deleted.
- **All ids are UUIDs**, so the phone app can create records offline (stage 5)
  and so the three Murimi systems can be joined without clashes.

## Stages

1. **Done:** businesses, team roles, farms, fields, seasons, crop plans, USD/ZiG/ZAR rates, change history.
2. **Done:** stock in batches (FIFO, USD and ZiG values), stock takes, undo, chemical safety intervals, inputs cost per crop plan, fuel use per machine with warnings.
3. **Done:** workers, daily work register and piece work (linked to crops), advances, pay runs with PAYE, AIDS levy and NSSA in US$ or ZiG, part-ZiG pay, NEC minimum wage warnings, printable payslips and a CSV for the accountant. Supervisors record work but never see wages.
4. **Done:** sales invoices and purchases in US$, ZiG or rand; VAT at 15.5% from 1 Jan 2026 (15% before) with standard, zero-rated and exempt lines; input VAT counted only with the supplier's fiscal receipt; fiscal receipt numbers on invoices (FDMS connection can plug into `erp_trade/fiscal.py` later); harvests into stock; reports for crop cost and profit per hectare, profit and loss, VAT and who owes what.
5. **Done:** offline phone app. The website installs on phones (PWA), keeps pages for offline use, and queues field records in an outbox that syncs safely (each record has its own id, so it is never saved twice). Android APK built by GitHub from `android/` (see `PHONE_APP.md`); going online is in `DEPLOY_PYTHONANYWHERE.md`.

Tax and wage figures change often. Have an accountant confirm them before using them for real returns.

## Back up

`db.sqlite3` holds everything. It is not in git on purpose, so copy it somewhere safe regularly.
