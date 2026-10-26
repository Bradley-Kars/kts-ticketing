# G4 Part 1 — Local Invoice & Receipt Generation Spec (2026-10-08)

Status: **docs + templates + tests only.** No numbering change until
Bradley approves the G4 proposal. No network calls of any kind. No
vault or ticketing data writes. Existing 4 invoices + 1 receipt
untouched.

## Inputs

- Company block: name, address, phone, email (adam-dewey template shape).
- Client block: name, address, email.
- Line items: description, qty, unit_price.
- Invoice metadata: date, due_date, terms, po_number, tax_rate,
  discount, notes, payment_info.
- `number`: **injected by the caller** (see Numbering).
- All tests use mock data only; no production client data is loaded.

## Pipeline

```
line total  = round_half_up(qty * unit_price, 2)
subtotal    = sum(line totals)
taxable     = subtotal - round_half_up(discount, 2)
tax         = round_half_up(taxable * tax_rate, 2)
total       = taxable + tax
```

Rounding: **Decimal, ROUND_HALF_UP, 2dp**, applied at line totals and
tax. Totals sum already-rounded values — no double-rounding drift.
Rendered money values are JSON-safe floats (matches template shape).

## Numbering (CURRENT scheme, unchanged this step)

- Invoice IDs: `INV-YYYYMMDD-NNN`; receipt IDs: `RCP-YYYYMMDD-NNN`.
- `NNN` is a **per-day monotonic sequence** from `core/counters.py`:
  `next_seq(key, scan_hint)` returns `max(stored, scan_hint) + 1` and
  persists the new value atomically BEFORE the caller uses the number
  (e.g. key `invoice:20261006`). Deleting the file never re-frees a
  number. Existing consumers: `core/events.py`, `core/api.py`.
- **This layer never calls counters** (that would mutate
  `id-counters.json`). `render_invoice` / `render_receipt` take the ID
  as a string and pass it through verbatim. Integration code assigns
  IDs at generation time once Bradley approves the scheme.
- Note: `~/Work/invoice-adam-dewey.json` carries a hand-typed sample
  number `INV-2026-1005-001` that does NOT match the live scheme —
  structure/amounts are the template, IDs come from counters.

## Receipt (on payment)

`number, receipt_date, invoice_number, ticket_id, client,
payment_method, notes` + `amount` (HALF_UP 2dp). Links the payment to
its invoice and ticket. Field set is a proposal — Bradley reviews with
the numbering scheme.

## Tax

Structural only: `tax_rate` defaults to `0` (adam template has 0).
A non-zero rate / which items are taxable is **Bradley's decision** —
listed as open.

## Explicit exclusions (this step)

- No network calls; `core/templates.py` source is asserted free of
  network and file-I/O primitives by test.
- No read of `config/.env`. No key material anywhere.
- No writes to vault, entities, indexes, invoices, or
  `id-counters.json` (proven by baseline manifests).
- **No payment-provider write path designed or built** — separate
  open decision for Bradley (the current key is read-only anyway).

## Test evidence

- RED: `tests/test_g4_generation.py` failed at import
  (`ImportError: cannot import name 'templates' from 'core'`).
- GREEN: 11 tests OK after `core/templates.py` landed.
- Covers: current-numbering pass-through, template key structure,
  line/subtotal/tax/total math, HALF_UP edges, discount-before-tax,
  receipt linkage + rounding, field-list contracts, source purity.
