"""G4 part 1: invoice/receipt generation tests (Bradley 2026-10-08).

Scope: local generation only - pure functions, mock data, IDs in the
CURRENT numbering format injected by the caller (no counters calls, no
file I/O, no Stripe import, no vault/ticketing data writes). Written
RED first against the generation spec, then green once core/templates
lands. Rounding: HALF_UP at 2dp on line totals and tax; totals sum the
already-rounded values (no double-rounding drift). Tax defaults to 0 -
a non-zero rate is Bradley's decision.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)

from core import templates as tpl  # noqa: E402

MOCK_COMPANY = {
    "name": "Kars Tech Solutions",
    "address": "501 Maple ST, Brainerd MN 56401",
    "phone": "+1 (555) 123-4567",
    "email": "office@example.invalid",
}
MOCK_CLIENT = {
    "name": "Test Client",
    "address": "1 Test Lane\nPhone: 000-000-0000",
    "email": "client@example.invalid",
}
MOCK_ITEMS = [
    {"description": "Diagnostic Fee (Waived - Repair Completed)",
     "qty": 1, "unit_price": 0.0},
    {"description": "Labor - mock work (2 hrs @ $65/hr)",
     "qty": 2, "unit_price": 65.0},
]
CURRENT_INVOICE_ID = "INV-20261008-003"
CURRENT_RECEIPT_ID = "RCP-20261008-001"


class TestInvoiceGeneration(unittest.TestCase):
    def _invoice(self, **kw):
        args = dict(
            number=CURRENT_INVOICE_ID,
            date="2026-10-08",
            company=MOCK_COMPANY,
            client=MOCK_CLIENT,
            items=MOCK_ITEMS,
            terms="Due Upon Receipt",
        )
        args.update(kw)
        return tpl.render_invoice(**args)

    def test_current_numbering_passes_through_verbatim(self):
        inv = self._invoice()
        self.assertEqual(inv["invoice"]["number"], CURRENT_INVOICE_ID)
        self.assertRegex(inv["invoice"]["number"],
                         r"^INV-\d{8}-\d{3}$")

    def test_structure_matches_adam_template_keys(self):
        inv = self._invoice()
        self.assertEqual(set(inv), {"company", "client", "invoice"})
        inv_fields = set(inv["invoice"])
        for f in ("number", "date", "due_date", "terms", "po_number",
                  "tax_rate", "items", "subtotal", "discount", "tax",
                  "total", "notes", "payment_info"):
            self.assertIn(f, inv_fields)
        for item in inv["invoice"]["items"]:
            for f in ("description", "qty", "unit_price", "total"):
                self.assertIn(f, item)

    def test_line_totals_and_sums(self):
        inv = self._invoice()
        totals = [it["total"] for it in inv["invoice"]["items"]]
        self.assertEqual(totals, [0.0, 130.0])
        self.assertEqual(inv["invoice"]["subtotal"], 130.0)
        self.assertEqual(inv["invoice"]["tax"], 0.0)
        self.assertEqual(inv["invoice"]["total"], 130.0)

    def test_rounding_half_up_at_two_decimals(self):
        inv = self._invoice(items=[{"description": "edge", "qty": 1,
                                    "unit_price": 0.005}])
        self.assertEqual(inv["invoice"]["items"][0]["total"], 0.01)
        inv2 = self._invoice(items=[{"description": "edge2", "qty": 3,
                                     "unit_price": 10.005}])
        # 30.015 -> HALF_UP -> 30.02
        self.assertEqual(inv2["invoice"]["items"][0]["total"], 30.02)

    def test_tax_defaults_zero_and_computes_when_set(self):
        inv = self._invoice()
        self.assertEqual(inv["invoice"]["tax_rate"], 0)
        self.assertEqual(inv["invoice"]["tax"], 0.0)
        taxed = self._invoice(tax_rate=0.07)
        self.assertEqual(taxed["invoice"]["tax"], 9.10)
        self.assertEqual(taxed["invoice"]["total"], 139.10)

    def test_discount_applied_before_tax(self):
        inv = self._invoice(discount=10.0, tax_rate=0.07)
        self.assertEqual(inv["invoice"]["discount"], 10.0)
        self.assertEqual(inv["invoice"]["tax"], 8.40)   # 120 * 0.07
        self.assertEqual(inv["invoice"]["total"], 128.40)

    def test_rendered_money_values_are_json_floats(self):
        inv = self._invoice(tax_rate=0.07)
        for v in (inv["invoice"]["subtotal"], inv["invoice"]["tax"],
                  inv["invoice"]["total"]):
            self.assertIsInstance(v, float)


class TestReceiptGeneration(unittest.TestCase):
    def test_receipt_current_numbering_and_link(self):
        r = tpl.render_receipt(
            number=CURRENT_RECEIPT_ID,
            receipt_date="2026-10-08",
            invoice_number=CURRENT_INVOICE_ID,
            ticket_id="TKT-2026-001",
            client=MOCK_CLIENT,
            payment_method="cash",
            amount=130.00,
            notes="mock payment",
        )
        self.assertEqual(r["number"], CURRENT_RECEIPT_ID)
        self.assertRegex(r["number"], r"^RCP-\d{8}-\d{3}$")
        self.assertEqual(r["invoice_number"], CURRENT_INVOICE_ID)
        self.assertEqual(r["amount"], 130.00)
        self.assertIsInstance(r["amount"], float)
        for f in ("number", "receipt_date", "invoice_number", "ticket_id",
                  "client", "payment_method", "amount", "notes"):
            self.assertIn(f, r)

    def test_receipt_amount_rounds_half_up(self):
        r = tpl.render_receipt(
            number=CURRENT_RECEIPT_ID, receipt_date="2026-10-08",
            invoice_number=CURRENT_INVOICE_ID, ticket_id="TKT-2026-001",
            client=MOCK_CLIENT, payment_method="card", amount=10.005,
        )
        self.assertEqual(r["amount"], 10.01)


class TestTemplateContracts(unittest.TestCase):
    def test_field_lists_exist(self):
        for f in ("number", "date", "terms", "items", "subtotal",
                  "discount", "tax", "total"):
            self.assertIn(f, tpl.INVOICE_FIELDS)
        for f in ("number", "receipt_date", "invoice_number",
                  "payment_method", "amount"):
            self.assertIn(f, tpl.RECEIPT_FIELDS)

    def test_templates_module_has_no_stripe(self):
        src = Path(tpl.__file__).read_text(encoding="utf-8").lower()
        self.assertNotIn("stripe", src)
        self.assertNotIn("urllib", src)
        self.assertNotIn("open(", src)  # pure functions: no file I/O


if __name__ == "__main__":
    unittest.main()
