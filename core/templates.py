"""Invoice and receipt templates — local generation, Bradley G4 part 1.

Pure functions only: every money value is computed with Decimal and
rounded HALF_UP at 2dp on line totals and tax; totals are the sum of
already-rounded values, so there is no double-rounding drift. Numbers
(invoice/receipt IDs) are injected by the caller — integration code
pulls them from core/counters.py under the CURRENT numbering scheme
until Bradley approves a new one. No file I/O, no network, no external
services. Tax defaults to 0; a non-zero rate is Bradley's decision.
"""

from decimal import Decimal, ROUND_HALF_UP

TWOPLACES = Decimal("0.01")

INVOICE_FIELDS = (
    "number", "date", "due_date", "terms", "po_number", "tax_rate",
    "items", "subtotal", "discount", "tax", "total", "notes",
    "payment_info",
)
INVOICE_ITEM_FIELDS = ("description", "qty", "unit_price", "total")
RECEIPT_FIELDS = (
    "number", "receipt_date", "invoice_number", "ticket_id", "client",
    "payment_method", "amount", "notes",
)


def money(value) -> Decimal:
    """Quantize to 2dp with HALF_UP (the rounding rule for this stack)."""
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def render_line(item: dict) -> dict:
    """One invoice line: total = qty * unit_price, HALF_UP at 2dp."""
    total = money(Decimal(str(item["qty"])) * Decimal(str(item["unit_price"])))
    return {
        "description": item["description"],
        "qty": item["qty"],
        "unit_price": item["unit_price"],
        "total": float(total),
    }


def render_invoice(
    number: str,
    date: str,
    company: dict,
    client: dict,
    items: list,
    terms: str = "",
    due_date: str = None,
    po_number: str = "",
    tax_rate=0,
    discount=0,
    notes: str = "",
    payment_info: str = "",
) -> dict:
    """Render an invoice dict matching the adam-dewey template shape.

    Pipeline: round each line -> subtotal -> subtract discount ->
    tax on the discounted base -> total. ID passes through verbatim.
    """
    lines = [render_line(i) for i in items]
    subtotal = money(
        sum((Decimal(str(ln["total"])) for ln in lines), Decimal("0"))
    )
    discount_m = money(discount)
    taxable = money(subtotal - discount_m)
    tax_rate_d = Decimal(str(tax_rate))
    tax_m = money(taxable * tax_rate_d)
    total = money(taxable + tax_m)
    return {
        "company": company,
        "client": client,
        "invoice": {
            "number": number,
            "date": date,
            "due_date": due_date if due_date is not None else date,
            "terms": terms,
            "po_number": po_number,
            "tax_rate": float(tax_rate_d),
            "items": lines,
            "subtotal": float(subtotal),
            "discount": float(discount_m),
            "tax": float(tax_m),
            "total": float(total),
            "notes": notes,
            "payment_info": payment_info,
        },
    }


def render_receipt(
    number: str,
    receipt_date: str,
    invoice_number: str,
    ticket_id: str,
    client,
    payment_method: str,
    amount,
    notes: str = "",
) -> dict:
    """Render a receipt dict: records payment against an invoice.

    Amount is quantized HALF_UP at 2dp. payment_method is one of
    cash/card/check/transfer labels supplied by the caller.
    """
    return {
        "number": number,
        "receipt_date": receipt_date,
        "invoice_number": invoice_number,
        "ticket_id": ticket_id,
        "client": client,
        "payment_method": payment_method,
        "amount": float(money(amount)),
        "notes": notes,
    }
