// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

// İtiraz adayları penceresi: faturalayan ve fatura başına gruplu, işaretle ve itiraz aç.
// Çizim saf (test: scratchpad/test_itiraz.mjs). Tasarım: docs/plans/kargo-maliyet-ekrani-ve-itiraz.md §4.5.
frappe.provide("erpnext_shipping.dispute_candidates");

(function (ns) {
	ns.METHOD = "erpnext_shipping.erpnext_shipping.dispute.";
	ns.STYLE = `<style id="cand-style">
		.cand-list { margin: 0; padding: 0; list-style: none; }
		.cand-group { padding: 10px 0 4px; font-weight: 600; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px;
			border-bottom: 1px solid var(--border-color, #d1d8dd); }
		.cand-item { padding: 8px 0; border-bottom: 1px solid var(--border-color, #d1d8dd); }
		.cand-item label { display: flex; gap: 10px; align-items: flex-start; margin: 0; font-weight: normal; cursor: pointer; }
		.cand-item input, .cand-group input { margin-top: 3px; flex: 0 0 auto; }
		.cand-body { flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
		.cand-row { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 12px; align-items: center; }
		.cand-num { font-variant-numeric: tabular-nums; white-space: nowrap; }
		.cand-sub { margin-top: 2px; }
	</style>`;

	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const money = (row, v) => format_currency(flt(v), row.currency || "EUR");

	// Faturalayan ve fatura numarası başına grup; sunucunun açacağı itirazla aynı bölme.
	ns.groups = function (rows) {
		const out = [];
		const index = {};
		for (const row of rows || []) {
			const key = `${String(row.billed_by || "").toLowerCase()}|${row.invoice_number || ""}`;
			if (!(key in index)) {
				index[key] = out.length;
				out.push({ key: key, billed_by: row.billed_by, invoice_number: row.invoice_number, rows: [] });
			}
			out[index[key]].rows.push(row);
		}
		return out;
	};

	ns.html = function (rows, days) {
		if (!(rows || []).length) {
			return `<p class="text-muted">${esc(__("No charge from the last {0} days looks wrong.", [days]))}</p>`;
		}
		const groups = ns.groups(rows)
			.map((g, gi) => {
				const total = g.rows.reduce((t, r) => t + flt(r.suggested), 0);
				const items = g.rows
					.map((r) => {
						const reasons = (r.reasons || [r.reason]).map((x) => __(x)).join(", ");
						const sub = [r.scan_date ? frappe.datetime.str_to_user(r.scan_date) : null, r.contents_summary]
							.filter(Boolean)
							.map(esc)
							.join(" · ");
						const ask = flt(r.suggested)
							? __("dispute {0} of {1}", [money(r, r.suggested), money(r, r.billed)])
							: __("billed {0}", [money(r, r.billed)]);
						return `<li class="cand-item"><label>
							<input type="checkbox" data-name="${esc(r.name)}" data-group="${gi}">
							<span class="cand-body">
								<span class="cand-row"><span><b>${esc(r.parcel_number)}</b> · ${esc(reasons)}</span>
									<span class="cand-num">${esc(ask)}</span></span>
								${sub ? `<div class="cand-sub text-muted">${sub}</div>` : ""}
								${r.evidence ? `<div class="cand-sub text-muted">${esc(r.evidence)}</div>` : ""}
							</span></label></li>`;
					})
					.join("");
				const head = __("{0} · invoice {1}", [g.billed_by, g.invoice_number]);
				const count = g.rows.length === 1 ? __("1 charge") : __("{0} charges", [g.rows.length]);
				return `<li class="cand-group"><input type="checkbox" data-select-group="${gi}" aria-label="${esc(
					__("Select all of {0}", [head])
				)}"><span>${esc(head)}</span><span class="text-muted cand-num">${esc(count)}${
					total ? " · " + esc(money(g.rows[0], total)) : ""
				}</span></li>${items}`;
			})
			.join("");
		return `${ns.STYLE}<ul class="cand-list">${groups}</ul>`;
	};

	ns.open = function (listview) {
		const days = 120;
		frappe
			.call({ method: ns.METHOD + "dispute_candidates", args: { days: days }, freeze: true })
			.then((r) => {
				const rows = r.message || [];
				const d = new frappe.ui.Dialog({
					title: __("Dispute Candidates"),
					size: "extra-large",
					fields: [
						{
							fieldtype: "HTML",
							fieldname: "intro",
							options: `<p>${esc(
								__(
									"Charges from the last {0} days that look wrong and are in no dispute yet. Tick the ones to dispute: one dispute is opened per party and invoice.",
									[days]
								)
							)}</p>`,
						},
						{ fieldtype: "HTML", fieldname: "list", options: ns.html(rows, days) },
					],
					primary_action_label: __("Create Dispute"),
					primary_action() {
						const names = d.$wrapper
							.find("input[data-name]:checked")
							.map((i, el) => $(el).attr("data-name"))
							.get();
						if (!names.length) {
							frappe.show_alert({ message: __("Tick at least one charge."), indicator: "orange" });
							return;
						}
						ns.create(names).then(() => {
							d.hide();
							listview && listview.refresh();
						});
					},
				});
				d.$wrapper.on("change", "input[data-select-group]", (e) => {
					const group = $(e.currentTarget).attr("data-select-group");
					d.$wrapper.find(`input[data-group="${group}"]`).prop("checked", e.currentTarget.checked);
				});
				if (!rows.length) d.get_primary_btn().prop("disabled", true);
				d.show();
			});
	};

	// İtiraz aç; tek itiraz çıkarsa onu aç, birden çoksa listeyi göster.
	ns.create = function (names) {
		return frappe
			.call({ method: ns.METHOD + "create_disputes", args: { cost_entries: names }, freeze: true })
			.then((r) => {
				const m = r.message || {};
				const disputes = m.disputes || [];
				const skipped = m.skipped || [];
				if (disputes.length === 1 && !skipped.length) {
					frappe.set_route("Form", "Carrier Invoice Dispute", disputes[0]);
					return m;
				}
				let msg = disputes.length
					? `<p>${esc(__("Disputes:"))} ${disputes
							.map((n) => `<a href="/app/carrier-invoice-dispute/${encodeURIComponent(n)}">${esc(n)}</a>`)
							.join(", ")}</p>`
					: "";
				if (skipped.length) {
					msg += `<p>${esc(__("Left out:"))}</p><ul>${skipped
						.map((s) => `<li>${esc(s.name)}: ${esc(s.why)}</li>`)
						.join("")}</ul>`;
				}
				frappe.msgprint({ title: __("Disputes"), indicator: skipped.length ? "orange" : "green", message: msg });
				return m;
			});
	};
})(erpnext_shipping.dispute_candidates);

frappe.listview_settings["Shipping Cost Entry"] = {
	add_fields: ["matched", "is_correction", "weight_surcharge", "reweigh", "surcharge_amount", "dispute", "dispute_status"],

	// Listede ve formun başlığında aynı durum. Önce "eşleşmedi" (ne olduğu bilinmeyen
	// maliyet), sonra itiraz, sonra düzeltme, sonra itiraz adayı olabilecek ek ücretler.
	get_indicator(doc) {
		if (!doc.matched) return [__("Not Matched"), "red", "matched,=,0"];
		if (doc.dispute && doc.dispute_status) {
			return [__("Dispute: {0}", [__(doc.dispute_status)]), "purple", `dispute_status,=,${doc.dispute_status}`];
		}
		if (doc.is_correction) return [__("Correction"), "blue", "is_correction,=,1"];
		if (doc.weight_surcharge || doc.reweigh) return [__("Weight/Size Surcharge"), "orange", "weight_surcharge,=,1"];
		if (flt(doc.surcharge_amount) > 0) return [__("Surcharge"), "yellow", "surcharge_amount,>,0"];
		return [__("Matched"), "green", "matched,=,1"];
	},

	onload: function (listview) {
		listview.page.add_inner_button(__("Import Invoice"), function () {
			const d = new frappe.ui.Dialog({
				title: __("Import Carrier Invoice"),
				fields: [
					{
						fieldtype: "Attach",
						fieldname: "file",
						label: __("Invoice File"),
						reqd: 1,
						description: __(
							"DPD detail (.xlsx), SendCloud invoice (.csv), FedEx invoice (.xml), or a .zip containing many of them. Format is auto-detected."
						),
					},
				],
				primary_action_label: __("Import"),
				primary_action(values) {
					frappe.call({
						method: "erpnext_shipping.erpnext_shipping.shipping_cost.import_invoice",
						freeze: true,
						freeze_message: __("Importing shipping costs..."),
						args: { file_url: values.file },
						callback: function (r) {
							if (r.exc || !r.message) return;
							d.hide();
							const m = r.message;
							let msg = __(
								"Files: {0}, Created: {1}, Updated: {2}<br>Matched shipments: {3} (by tracking: {4}, by order no: {5})<br>Ambiguous: {6}, Unmatched parcels: {7}",
								[
									m.files,
									m.created,
									m.updated,
									m.matched_shipments,
									m.matched_by_tracking,
									m.matched_by_order,
									m.ambiguous,
									m.unmatched_parcels,
								]
							);
							if (m.errors && m.errors.length) {
								msg +=
									"<br><br><b>" +
									__("Errors ({0}):", [m.errors.length]) +
									"</b><br>" +
									m.errors
										.slice(0, 10)
										.map((e) => frappe.utils.escape_html(e))
										.join("<br>");
							}
							frappe.msgprint({
								title: __("Import Complete"),
								indicator: m.errors && m.errors.length ? "orange" : "green",
								message: msg,
							});
							listview.refresh();
						},
					});
				},
			});
			d.show();
		});

		listview.page.add_inner_button(__("Dispute Candidates"), function () {
			erpnext_shipping.dispute_candidates.open(listview);
		});

		listview.page.add_inner_button(__("Fetch SendCloud Invoices"), function () {
			frappe.call({
				method: "erpnext_shipping.erpnext_shipping.sendcloud_invoices.fetch_now",
				callback: function (r) {
					if (r.message) frappe.show_alert({ message: r.message, indicator: "blue" }, 8);
				},
			});
		});

		listview.page.add_inner_button(__("Re-match Unmatched"), function () {
			frappe.call({
				method: "erpnext_shipping.erpnext_shipping.shipping_cost.rematch_unmatched",
				freeze: true,
				freeze_message: __("Re-matching..."),
				callback: function (r) {
					if (r.exc || !r.message) return;
					frappe.msgprint({
						title: __("Re-match Complete"),
						indicator: "blue",
						message: __("Matched: {0}, Still unmatched: {1}", [
							r.message.matched,
							r.message.remaining,
						]),
					});
					listview.refresh();
				},
			});
		});
	},
};
