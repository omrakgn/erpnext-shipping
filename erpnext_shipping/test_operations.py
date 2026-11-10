"""Shipping cards of the operations board. No site needed:

python -m unittest erpnext_shipping.test_operations
"""

import datetime
import sys
import types
import unittest
from unittest import mock

from erpnext_shipping import operations as ops

_d = ops.frappe._dict
TODAY = datetime.date(2026, 11, 20)


def ctx(**values):
	now = datetime.datetime(2026, 11, 20, 9, 30)  # noqa: DTZ001 - naive site time, as Frappe
	out = _d(today=TODAY, now=now, draft_after_days=1)
	out.update(values)
	return out


class Lister:
	"""frappe.get_all: counts answer n, plucks answer names from a table keyed by doctype and filters."""

	def __init__(self, counts=None, plucks=None):
		self.calls = []
		self.counts = counts or {}
		self.plucks = plucks or {}

	def __call__(self, doctype, filters=None, fields=None, pluck=None, **kwargs):
		self.calls.append((doctype, filters))
		if pluck:
			for (dt, match), names in self.plucks.items():
				if dt == doctype and all(filters.get(k) == v for k, v in match):
					return list(names)
			return []
		return [_d(n=self.counts.get(doctype, 0))]


class CardTests(unittest.TestCase):
	def test_waiting_for_label(self):
		lister = Lister({"Shipment": 4})
		with mock.patch.object(ops.frappe, "get_all", lister):
			item = ops.waiting_for_label(ctx())
		self.assertEqual(
			item["filters"],
			{
				"docstatus": 1,
				"status": ["!=", "Cancelled"],
				"shipment_id": ["is", "not set"],
				"awb_number": ["is", "not set"],
				"shipping_transport_mode": ["is", "not set"],
				"pickup_date": ["<=", "2026-11-20"],
			},
		)
		self.assertEqual(item["count"], 4)
		self.assertEqual(lister.calls[0], ("Shipment", item["filters"]))

	def test_drafts_use_the_board_limit(self):
		with mock.patch.object(ops.frappe, "get_all", Lister()):
			self.assertEqual(
				ops.drafts(ctx(draft_after_days=3))["filters"],
				{"docstatus": 0, "creation": ["<", "2026-11-17"]},
			)
			self.assertEqual(
				ops.drafts(ctx(draft_after_days=None))["filters"]["creation"], ["<", "2026-11-19"]
			)

	def test_late_leaves_out_what_the_stored_flag_wrongly_counts(self):
		with mock.patch.object(ops.frappe, "get_all", Lister()):
			filters = ops.late(ctx())["filters"]
		self.assertEqual(filters["custom_sla_status"], "Breached")
		self.assertEqual(filters["custom_returned_to_sender"], 0)
		self.assertEqual(filters["custom_presumed_lost"], 0)
		self.assertEqual(filters["custom_is_return"], 0)
		self.assertEqual(filters["tracking_status"], ["not in", ["Delivered", "Returned", "Lost"]])
		self.assertEqual(filters["pickup_date"], [">=", "2026-07-23"])

	def test_lost_without_any_claim(self):
		lister = Lister(
			plucks={
				("Shipment Loss Claim", ()): ["SH-2"],
				("Shipment", (("custom_presumed_lost", 1),)): ["SH-1", "SH-2", "SH-3"],
			}
		)
		with mock.patch.object(ops.frappe, "get_all", lister):
			item = ops.lost_without_claim()
		self.assertEqual(item["filters"], {"name": ["in", ["SH-1", "SH-3"]]})
		self.assertEqual(item["tone"], "critical")
		shipments = next(f for d, f in lister.calls if d == "Shipment")
		self.assertEqual(shipments["custom_returned_to_sender"], 0)

	def test_claim_deadlines(self):
		with mock.patch.object(ops.frappe, "get_all", Lister({"Shipment Loss Claim": 2})):
			item = ops.claim_deadlines(ctx())
		self.assertEqual(item["filters"]["claim_deadline"], ["<=", "2026-11-23"])
		self.assertEqual(item["filters"]["submitted_date"], ["is", "not set"])
		self.assertIn("Submitted to Carrier", item["filters"]["status"][1])
		self.assertNotIn("Draft", item["filters"]["status"][1])
		self.assertEqual(item["count"], 2)

	def test_disputes_late_draft_or_unanswered_once(self):
		lister = Lister(
			plucks={
				("Carrier Invoice Dispute", (("status", "Draft"),)): ["CID-1"],
				("Carrier Invoice Dispute", (("status", "Sent"),)): ["CID-2", "CID-1"],
			}
		)
		with mock.patch.object(ops.frappe, "get_all", lister):
			item = ops.disputes_late(ctx())
		self.assertEqual(item["filters"], {"name": ["in", ["CID-1", "CID-2"]]})
		self.assertEqual(lister.calls[0][1], {"status": "Draft", "deadline": ["<", "2026-11-20"]})
		self.assertEqual(lister.calls[1][1], {"status": "Sent", "answer_due": ["<", "2026-11-20"]})

	def test_returned_parcels_only_when_they_bounced(self):
		rows = [
			{"shipment": "SH-1", "gap_days": 0},
			{"shipment": "SH-2", "gap_days": 2},
			{"shipment": "SH-3", "gap_days": 3},
			{"shipment": "SH-4", "gap_days": None},
			{"shipment": "SH-1", "gap_days": 1},
		]
		fake = types.ModuleType("erpnext_shipping.erpnext_shipping.loss")
		fake.find_returns_without_shipment_flag = mock.Mock(return_value=rows)
		cache = mock.Mock(get_value=mock.Mock(return_value=None))
		with (
			mock.patch.dict(sys.modules, {"erpnext_shipping.erpnext_shipping.loss": fake}),
			mock.patch.object(ops.frappe, "cache", cache, create=True),
		):
			item = ops.returned_unflagged()
		self.assertEqual(item["filters"], {"name": ["in", ["SH-1", "SH-2"]]})
		cache.set_value.assert_called_once_with(ops.RETURNS_CACHE_KEY, ["SH-1", "SH-2"], expires_in_sec=600)

	def test_returned_parcels_come_from_the_cache(self):
		cache = mock.Mock(get_value=mock.Mock(return_value=["SH-9"]))
		with mock.patch.object(ops.frappe, "cache", cache, create=True):
			self.assertEqual(ops.cached_returned_shipments(), ["SH-9"])

	def test_all_cards_are_in_the_shipping_group(self):
		with (
			mock.patch.object(ops.frappe, "get_all", Lister()),
			mock.patch.object(ops, "cached_returned_shipments", return_value=[]),
		):
			cards = ops.cards(ctx())
		self.assertEqual({c["group"] for c in cards}, {"Shipping"})
		self.assertEqual(len({c["key"] for c in cards}), 7)

	def test_hook_points_here(self):
		from erpnext_shipping import hooks

		self.assertEqual(hooks.operations_board_sources, ["erpnext_shipping.operations.cards"])


if __name__ == "__main__":
	unittest.main()
