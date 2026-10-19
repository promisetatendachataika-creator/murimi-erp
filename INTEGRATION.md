# Joining Murimi Farm ERP with the Hub and the Market

The ERP is built to be copied into Murimi Intelligent Hub at the end.
This file lists what will need to change when that happens. If this list
keeps growing, the modules are drifting apart.

## What copies straight across

- Apps `erp_core`, `erp_finance`, `erp_inventory`, `erp_labour`, `erp_trade` (and later `erp_sync`).
- `murimi_common/` (shared rules and lists).
- All ERP tables use UUID primary keys and are prefixed `erp_core_...`, `erp_finance_...`, so they can't clash.
- ERP code refers to people only through `settings.AUTH_USER_MODEL`, never a User class.
- API under `/api/erp/...`, with the Hub's error format and session + CSRF.

## Will need changing

1. **Users.** The ERP's `accounts.User` copies the Hub's fields (phone, username, full_name, email, role,
   whatsapp_consent, phone_verified_at). When joined, drop the ERP's `accounts` app, point ERP tables at
   the Hub's users, and move people across by matching phone numbers.
2. **Crops.** `murimi_common/choices.py` adds `ERP_EXTRA_CROPS` (Wheat, Soya beans, Sorghum, Groundnuts,
   Paprika, Macadamia, Blueberries, Onions, Cabbages, Pastures). The Hub's `accounts/choices.py` and the
   Market's product list must adopt them. The Market also has POULTRY (Broilers, Eggs).
3. **Organisations become the shared workspace.** `erp_core.Organisation` and `Membership` are designed to be
   used by the Hub and Market too (the SaaS plan). The Hub's organisation-level verification should set a
   "verified" mark on the ERP organisation.
4. **Exchange rates.** The Hub's `market.PriceReport` stores US$ and ZiG prices; after joining, Murimi-wide
   rates (organisation empty) can be shared by all modules.
5. **The Market's own `accounts` app** uses a different log-in backend (also accepts email). Pick one backend
   for the joined system.

## Links to build after joining

- Hub price reports → ERP harvest valuation and the Market's fair-price meter.
- ERP harvests (`erp_trade.Harvest`) and produce in stock → one-click Market listings; Market orders → ERP sales invoices (`erp_trade.Document`, kind "sale"), with the Market buyer becoming an ERP `Contact`.
- Hub workers and their WhatsApp numbers → payslips sent by WhatsApp (uses the Hub's WhatsApp sender).
- Market input sellers (seed, fertiliser) → ERP "Receive stock" with supplier and price filled in.
- ERP stock values (`erp_inventory`) → Hub investor reports: inputs used against each crop plan's budget.
- Hub haulage jobs ↔ Market deliveries and ERP transport costs.
- Hub crowd-farming projects ↔ ERP seasons and crop plans: investors see budget against actual costs.
