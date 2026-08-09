# Copyright (c) 2026, Scarnatti and contributors
# For license information, please see license.txt

"""
Turn imported carrier invoices into Purchase Invoices.

Carrier invoices arrive as parcel-level `Shipping Cost Entry` rows, matched to
the Shipment or Delivery Note they belong to. Useful for cost-per-parcel
reporting, but until now none of it reached the ledger: freight was invisible
in the accounts and the carrier balances could not be reconciled against the
bank.

One Purchase Invoice per carrier invoice number. The entries stay where they
are as the parcel-level breakdown; the invoice is the accounting summary.

Two things decide how a line is booked:

  who invoices us     `billed_via` first, then `carrier`. A DPD parcel booked
                      through SendCloud is billed by SendCloud, so the debt is
                      SendCloud's even though DPD carried it.

  which cost centre   the matched delivery's, so freight lands wherever the
                      sale did. Unmatched parcels fall back to the setting.

Correction lines (`is_correction`) net against the charges in the same cost
centre rather than becoming separate rows — otherwise the account shows the
gross freight and never what it actually cost.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, today


def _settings():
    return frappe.get_single("Shipping Cost Settings")


def _carrier_accounts(settings):
    """Lower-cased carrier name -> mapping row."""
    out = {}
    for row in settings.get("carrier_accounts") or []:
        if row.carrier:
            out[row.carrier.strip().lower()] = row
    return out


def _account_for(entry, accounts):
    """
    Resolve the mapping row for one entry.

    `billed_via` wins: it names the party that actually invoiced us. Only when
    it is empty does the carrier itself do the billing.
    """
    for value in (entry.get("billed_via"), entry.get("carrier")):
        if value:
            row = accounts.get(value.strip().lower())
            if row:
                return row
    return None


def _cost_center_for(entry, fallback):
    """
    Freight follows the sale: take the cost centre from the delivery this
    parcel was matched to. A parcel nobody could match has no channel to
    belong to, so it falls back to the setting.
    """
    if entry.get("delivery_note"):
        cost_center = frappe.db.get_value(
            "Delivery Note Item", {"parent": entry.delivery_note}, "cost_center"
        )
        if cost_center:
            return cost_center
    return fallback


@frappe.whitelist()
def create_invoice(invoice_number, submit=None):
    """
    Create the Purchase Invoice for one carrier invoice number.

    Idempotent: entries already billed are skipped, and an invoice whose
    `bill_no` is already on the supplier is not created twice.
    """
    settings = _settings()
    if not settings.enable_cost_invoicing:
        frappe.throw(_("Enable 'Create Purchase Invoices from Carrier Invoices' in "
                       "Shipping Cost Settings first."))

    company = settings.company or frappe.defaults.get_user_default("Company")
    if not company:
        frappe.throw(_("Set Company in Shipping Cost Settings."))

    entries = frappe.get_all(
        "Shipping Cost Entry",
        filters={"invoice_number": invoice_number,
                 "purchase_invoice": ["in", [None, ""]]},
        fields=["name", "carrier", "billed_via", "delivery_note", "scan_date",
                "total_net_amount", "is_correction", "currency"],
    )
    if not entries:
        return None

    accounts = _carrier_accounts(settings)
    cutover = getdate(settings.cutover_date) if settings.cutover_date else None
    fallback_cc = settings.default_cost_center or frappe.db.get_value(
        "Company", company, "cost_center"
    )

    mapping = None
    buckets = {}
    billed = []
    skipped = {"before cutover": 0, "no amount": 0}
    unmapped = set()
    latest_date = None

    for entry in entries:
        row = _account_for(entry, accounts)
        if not row:
            unmapped.add((entry.get("billed_via") or entry.get("carrier") or "(blank)"))
            continue
        # Every line on one carrier invoice bills the same party; if a file ever
        # mixes them the invoice would be wrong, so say so rather than guess.
        if mapping and row.supplier != mapping.supplier:
            frappe.throw(
                _("Carrier invoice {0} names more than one supplier ({1}, {2}). "
                  "Split it manually.").format(invoice_number, mapping.supplier, row.supplier)
            )
        mapping = row

        scan_date = getdate(entry.scan_date) if entry.scan_date else None
        if cutover and scan_date and scan_date < cutover:
            skipped["before cutover"] += 1
            continue

        amount = flt(entry.total_net_amount)
        if not amount:
            skipped["no amount"] += 1
            billed.append(entry.name)
            continue

        if scan_date and (not latest_date or scan_date > latest_date):
            latest_date = scan_date

        cost_center = _cost_center_for(entry, fallback_cc)
        # Summed at full precision; rounded once per bucket at the end.
        buckets[cost_center] = buckets.get(cost_center, 0) + amount
        billed.append(entry.name)

    if unmapped:
        if settings.unmapped_carrier_action == "Skip":
            frappe.logger().warning(
                f"Shipping invoice {invoice_number}: unmapped carriers {sorted(unmapped)}"
            )
        else:
            frappe.throw(
                _("Carrier invoice {0} names carriers that are not in the Carrier Accounts "
                  "table: {1}.<br><br>Add them in Shipping Cost Settings. They are not "
                  "guessed, because booking freight against the wrong supplier reports "
                  "no error.").format(invoice_number, ", ".join(sorted(unmapped)))
            )

    if not mapping:
        return None

    charges = {}
    for cost_center in buckets:
        value = flt(buckets[cost_center], 2)
        if value:
            charges[cost_center] = value

    if not charges:
        # Nothing in scope rather than nothing owed: every line was filtered
        # out, almost always by the cutover date. Distinct from a credit
        # balance below, which is a real situation needing a real decision.
        return None

    total = flt(sum(charges.values()), 2)
    if total <= 0:
        # A carrier invoice that nets to a credit is a refund, and the right
        # document is a debit note. Left to a human rather than guessed at.
        frappe.throw(
            _("Carrier invoice {0} nets to {1}, a credit rather than a charge. "
              "Raise a debit note manually.").format(invoice_number, total)
        )

    existing = frappe.db.get_value(
        "Purchase Invoice",
        {"bill_no": invoice_number, "supplier": mapping.supplier, "docstatus": ["<", 2]},
        "name",
    )
    if existing:
        for name in billed:
            frappe.db.set_value("Shipping Cost Entry", name, "purchase_invoice", existing)
        frappe.db.commit()
        return existing

    uom = "Nos" if frappe.db.exists("UOM", "Nos") else frappe.db.get_value("UOM", {}, "name")
    posting_date = latest_date or today()

    invoice = frappe.new_doc("Purchase Invoice")
    invoice.company = company
    invoice.supplier = mapping.supplier
    invoice.bill_no = invoice_number
    invoice.bill_date = posting_date
    invoice.set_posting_time = 1
    invoice.posting_date = posting_date

    for cost_center in sorted(charges):
        invoice.append("items", {
            "item_name": _("Freight"),
            "description": _("Carrier invoice {0}").format(invoice_number),
            "qty": 1,
            "uom": uom,
            "conversion_factor": 1,
            "rate": charges[cost_center],
            "expense_account": mapping.expense_account,
            "cost_center": cost_center,
        })

    if mapping.purchase_tax_template:
        from erpnext.controllers.accounts_controller import get_taxes_and_charges

        invoice.taxes_and_charges = mapping.purchase_tax_template
        invoice.set("taxes", get_taxes_and_charges(
            "Purchase Taxes and Charges Template", mapping.purchase_tax_template
        ))

    invoice.insert(ignore_permissions=True)

    # Linked before submitting: a submit that raises would otherwise leave the
    # invoice behind with the entries unbilled, and the next run would create a
    # second one for the same freight.
    for name in billed:
        frappe.db.set_value("Shipping Cost Entry", name, "purchase_invoice", invoice.name)
    frappe.db.commit()

    if submit is None:
        submit = settings.auto_submit_invoices
    if submit:
        invoice.submit()
        frappe.db.commit()

    return invoice.name


@frappe.whitelist()
def process_pending(limit=50):
    """
    Invoice every carrier invoice number that still has unbilled entries.

    One failure does not stop the rest: each invoice is reported on its own so
    a single unmapped carrier cannot block a whole period.
    """
    limit = int(limit)
    numbers = frappe.db.sql(
        """
        SELECT DISTINCT invoice_number
        FROM `tabShipping Cost Entry`
        WHERE IFNULL(invoice_number, '') != ''
          AND IFNULL(purchase_invoice, '') = ''
        ORDER BY invoice_number
        LIMIT %(limit)s
        """,
        {"limit": limit},
        pluck=True,
    )

    results = {"created": [], "skipped": [], "failed": []}
    for number in numbers:
        try:
            name = create_invoice(number)
            if name:
                results["created"].append({"invoice_number": number, "purchase_invoice": name})
            else:
                results["skipped"].append(number)
        except Exception as exc:
            frappe.db.rollback()
            results["failed"].append({"invoice_number": number, "error": str(exc)})
            frappe.log_error(
                title="Shipping cost accounting",
                message=f"{number}: {frappe.get_traceback()}",
            )
    return results
