# Copyright (c) 2026, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe

FIELDS = ("content", "icon", "label", "title", "public", "sequence_id")
CHILD_TABLES = ("links", "shortcuts", "charts", "number_cards")


def _row_ok(table, row):
	"""Referansı Link ile doğrulanan child satırları (number card / chart) yalnızca
	hedef varsa kabul et; yoksa workspace kaydı LinkValidationError vermesin."""
	if table == "number_cards":
		return frappe.db.exists("Number Card", row.get("number_card_name"))
	if table == "charts":
		return frappe.db.exists("Dashboard Chart", row.get("chart_name"))
	return True


def _apply_rows(doc, data):
	for table in CHILD_TABLES:
		doc.set(table, [])
		for row in data.get(table) or []:
			if _row_ok(table, row):
				doc.append(table, row)


def _filter_content(data):
	"""content bloklarından, hedefi (card/chart) mevcut olmayanları çıkar. Aksi halde
	content bir karta referans verip child tabloda bulunmayınca workspace render'ı
	komple kırılıyor (hiçbir kart/grafik görünmüyor)."""
	kept_cards = {
		r.get("number_card_name")
		for r in data.get("number_cards") or []
		if _row_ok("number_cards", r)
	}
	kept_charts = {
		r.get("chart_name") for r in data.get("charts") or [] if _row_ok("charts", r)
	}
	try:
		blocks = json.loads(data.get("content") or "[]")
	except Exception:
		return
	filtered = []
	for b in blocks:
		bdata = b.get("data") or {}
		if b.get("type") == "number_card" and bdata.get("number_card_name") not in kept_cards:
			continue
		if b.get("type") == "chart" and bdata.get("chart_name") not in kept_charts:
			continue
		filtered.append(b)
	data["content"] = json.dumps(filtered)


def execute():
	"""Create or update the standard "Shipping" Workspace from shipping.json so the
	links, shortcuts, number cards and chart stay in sync on migrate. References to
	missing cards/charts are skipped (both in child tables and in content) so the
	save never fails and the workspace still renders."""
	path = frappe.get_app_path(
		"erpnext_shipping", "erpnext_shipping", "workspace", "shipping", "shipping.json"
	)
	try:
		with open(path, encoding="utf-8") as f:
			data = json.load(f)
	except FileNotFoundError:
		return

	_filter_content(data)

	if frappe.db.exists("Workspace", "Shipping"):
		doc = frappe.get_doc("Workspace", "Shipping")
		for field in FIELDS:
			doc.set(field, data.get(field))
		_apply_rows(doc, data)
		doc.flags.ignore_permissions = True
		doc.save()
	else:
		doc = frappe.get_doc(data)
		_apply_rows(doc, data)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_if_duplicate=True)
