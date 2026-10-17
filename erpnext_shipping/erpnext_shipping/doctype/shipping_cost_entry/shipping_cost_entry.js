// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

// Kargo maliyet kaydı: bir bakışta ne, kime gitti, ne ödendi, fazla mı ödendi.
// Tasarım: docs/plans/kargo-maliyet-ekrani-ve-itiraz.md §3. ERPNext'in kendi gösterge
// ve tablo sınıfları; yeni renk yok. Çizim fonksiyonları saf: belge alır, HTML döner.

frappe.provide("erpnext_shipping.cost_entry");

(function (ns) {
	// Faturalanan ağırlık beyanı bu kadar aşarsa işaretlenir (plan §2, karar 7).
	ns.WEIGHT_DIFF_KG = 1;
	ns.WEIGHT_DIFF_PCT = 10;
	// Yalnız bu kaynaklarda ürün tablosu tek koliyi gösterir; "Shipment (all parcels)"
	// bütün gönderiyi taşır ve beyanla karşılaştırılamaz.
	ns.PARCEL_SOURCES = ["Parcel", "Shipment"];

	ns.STYLE = `<style id="sce-style">
		.sce-summary { display: flex; flex-direction: column; gap: var(--padding-sm, 8px); margin-bottom: var(--padding-sm, 8px); }
		.sce-headline { display: flex; align-items: baseline; flex-wrap: wrap; gap: var(--padding-md, 12px); }
		.sce-amount { font-size: var(--text-2xl, 20px); font-weight: 600; color: var(--text-color); font-variant-numeric: tabular-nums; }
		.sce-chips { display: flex; flex-wrap: wrap; gap: var(--padding-xs, 6px); }
		.sce-scroll { max-width: 640px; overflow-x: auto; }
		.sce-table { margin-bottom: var(--padding-xs, 6px); }
		.sce-num { font-variant-numeric: tabular-nums; white-space: nowrap; }
		.sce-figure { font-variant-numeric: tabular-nums; }
	</style>`;

	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const money = (doc, v) => format_currency(flt(v), doc.currency || "EUR");
	const kg = (v) => __("{0} kg", [format_number(flt(v), null, 1)]);
	const pill = (label, color) => `<span class="indicator-pill ${color}">${esc(label)}</span>`;
	const muted = (text) => `<span class="text-muted">${esc(text)}</span>`;

	ns.declared_weight = function (doc) {
		if (!ns.PARCEL_SOURCES.includes(doc.contents_source)) return null;
		const rows = doc.items || [];
		if (!rows.length || rows.some((r) => !(flt(r.unit_weight) > 0))) return null;
		return rows.reduce((t, r) => t + flt(r.unit_weight) * flt(r.qty), 0);
	};

	// İtiraz edilebilir: faturası belli, tutarı artı, düzeltme değil ve açık bir itirazda değil.
	ns.OPEN_DISPUTE = ["Draft", "Sent", "Accepted", "Partly Accepted"];
	ns.can_dispute = function (doc) {
		if (!doc.invoice_number || !(flt(doc.total_net_amount) > 0) || doc.is_correction) return false;
		return !(doc.dispute && ns.OPEN_DISPUTE.includes(doc.dispute_status));
	};

	ns.invoiced_weight = function (doc) {
		return flt(doc.corrected_weight) > 0 ? flt(doc.corrected_weight) : flt(doc.invoicing_weight);
	};

	ns.weight_excess = function (declared, invoiced) {
		if (!(declared > 0) || !(invoiced > 0)) return null;
		const diff = invoiced - declared;
		const over = diff > ns.WEIGHT_DIFF_KG || (diff / declared) * 100 > ns.WEIGHT_DIFF_PCT;
		return { diff: diff, flagged: diff > 0 && over };
	};

	ns.summary_html = function (doc) {
		const carrier = doc.carrier || __("Unknown carrier");
		const who =
			doc.billed_via && doc.billed_via !== doc.carrier
				? __("{0}, billed by {1}", [carrier, doc.billed_via])
				: carrier;
		const meta = [esc(who)];
		if (doc.scan_date) meta.push(esc(frappe.datetime.str_to_user(doc.scan_date)));

		const contents = doc.contents_summary
			? `<div>${esc(doc.contents_summary)}</div>`
			: `<div>${muted(__("Products unknown: the parcel is not matched to a shipment or delivery note."))}</div>`;

		const chips = [];
		if (!doc.matched) chips.push(pill(__("Not matched"), "red"));
		else chips.push(pill(__("Matched by {0}", [__(doc.match_method || "Manual")]), "gray"));
		if (doc.dispute) chips.push(pill(__("Dispute {0}: {1}", [doc.dispute, __(doc.dispute_status || "Draft")]), "purple"));
		if (doc.is_correction) chips.push(pill(__("Correction"), "blue"));
		if (flt(doc.surcharge_amount)) chips.push(pill(__("Surcharges {0}", [money(doc, doc.surcharge_amount)]), "orange"));
		if (doc.weight_surcharge) chips.push(pill(__("Weight/size surcharge"), "orange"));
		if (doc.reweigh) chips.push(pill(__("Re-weighed"), "orange"));
		const base = ns.baseline(doc);
		const excess = base && ns.weight_excess(base.weight, ns.invoiced_weight(doc));
		if (excess && excess.flagged) {
			const text =
				base.source === "label"
					? __("Billed {0} over the label weight", [kg(excess.diff)])
					: __("Billed {0} over declared", [kg(excess.diff)]);
			chips.push(pill(text, "red"));
		}
		if ((doc.receiver_source || "").includes("Order")) chips.push(pill(__("Receiver completed from order"), "gray"));

		return `<div class="sce-summary">
			<div class="sce-headline">
				<span class="sce-amount">${esc(money(doc, doc.total_net_amount))}</span>
				<span class="text-muted">${meta.join(" · ")}</span>
			</div>
			${contents}
			<div class="sce-chips">${chips.join("")}</div>
		</div>`;
	};

	ns.breakdown_rows = function (doc) {
		let data = {};
		try {
			data = JSON.parse(doc.charge_breakdown || "{}") || {};
		} catch (e) {
			data = {};
		}
		return Object.entries(data)
			.filter(([, v]) => flt(v) !== 0)
			.map(([k, v]) => ({ label: k, amount: flt(v) }));
	};

	ns.breakdown_html = function (doc) {
		const rows = ns.breakdown_rows(doc);
		if (!rows.length) return `<p>${muted(__("The invoice gives no breakdown for this line."))}</p>`;
		const total = rows.reduce((t, r) => t + r.amount, 0);
		const body = rows
			.map((r) => `<tr><td>${esc(r.label)}</td><td class="text-right sce-num">${esc(money(doc, r.amount))}</td></tr>`)
			.join("");
		return `<div class="sce-scroll"><table class="table table-sm sce-table">
			<tbody>${body}</tbody>
			<tfoot><tr><th>${esc(__("Total"))}</th><th class="text-right sce-num">${esc(money(doc, total))}</th></tr></tfoot>
		</table></div>`;
	};

	// Etikette yazan ağırlık, taşıyıcıya bizim beyan ettiğimizdir: itirazın kanıtı.
	// Yoksa ürünlerin koli şablonları.
	ns.label_weight = function (doc) {
		return flt(doc.sendcloud_weight) > 0 ? flt(doc.sendcloud_weight) : null;
	};

	ns.baseline = function (doc) {
		const label = ns.label_weight(doc);
		if (label) return { weight: label, source: "label" };
		const declared = ns.declared_weight(doc);
		return declared != null ? { weight: declared, source: "products" } : null;
	};

	ns.measure_note = function (doc, declared) {
		if (ns.label_weight(doc))
			return __("Difference: invoiced against the weight on the SendCloud label, which is what we declared to the carrier.");
		if (declared != null) return __("Declared: each product's parcel template, else its own weight, times the quantity in Contents.");
		if (doc.contents_source === "Shipment (all parcels)")
			return __("Declared weight is not compared: this parcel cannot be told apart from the rest of its shipment.");
		if ((doc.items || []).length) return __("Declared weight is not compared: a product in this parcel has no declared weight.");
		return __("Declared weight is not compared: the parcel's products are unknown.");
	};

	const dims = (l, w, h, girth) => {
		if (!(flt(l) && flt(w) && flt(h))) return null;
		return (
			__("{0} x {1} x {2} cm", [flt(l), flt(w), flt(h)]) + (flt(girth) ? " " + __("(girth {0} cm)", [flt(girth)]) : "")
		);
	};

	ns.measure_html = function (doc) {
		const declared = ns.declared_weight(doc);
		const label = ns.label_weight(doc);
		const invoiced = ns.invoiced_weight(doc);
		const base = ns.baseline(doc);
		const excess = base && ns.weight_excess(base.weight, invoiced);
		const notOnInvoice = muted(__("not on the invoice"));

		let invoicedCell = invoiced > 0 ? esc(kg(invoiced)) : notOnInvoice;
		if (flt(doc.corrected_weight) > 0 && flt(doc.invoicing_weight) > 0 && flt(doc.corrected_weight) !== flt(doc.invoicing_weight)) {
			invoicedCell += `<div class="small">${muted(__("(re-weighed, first {0})", [kg(doc.invoicing_weight)]))}</div>`;
		}
		let diffCell = "";
		if (excess) {
			const text = (excess.diff > 0 ? "+" : "") + kg(excess.diff);
			diffCell = excess.flagged ? pill(text, "red") : esc(text);
		}
		const labelDims = dims(doc.sendcloud_length, doc.sendcloud_width, doc.sendcloud_height);
		// Etiket sütunu yalnız SendCloud'dan bilgi geldiyse: öteki kayıtlarda boş bir sütun
		// telefonda farkı ekrandan iterdi.
		const hasLabel = Boolean(label || labelDims);
		const labelCell = (v, cls) => (hasLabel ? `<td class="${cls}">${v ? esc(v) : muted(__("unknown"))}</td>` : "");
		const invoicedDims = dims(doc.length, doc.width, doc.height, doc.girth);

		return `<div class="sce-scroll"><table class="table table-sm sce-table">
			<thead><tr><th></th><th>${esc(__("Products"))}</th>${hasLabel ? `<th>${esc(__("SendCloud Label"))}</th>` : ""}<th>${esc(__("Invoiced"))}</th><th>${esc(__("Difference"))}</th></tr></thead>
			<tbody>
				<tr><td>${esc(__("Weight"))}</td>
					<td class="sce-num">${declared != null ? esc(kg(declared)) : muted(__("unknown"))}</td>
					${labelCell(label && kg(label), "sce-num")}
					<td class="sce-figure">${invoicedCell}</td>
					<td class="sce-num">${diffCell}</td></tr>
				<tr><td>${esc(__("Dimensions"))}</td>
					<td>${muted(__("unknown"))}</td>
					${labelCell(labelDims, "sce-figure")}
					<td class="sce-figure">${invoicedDims ? esc(invoicedDims) : notOnInvoice}</td>
					<td></td></tr>
			</tbody>
		</table></div>
		<p class="small">${muted(ns.measure_note(doc, declared))}</p>`;
	};
})(erpnext_shipping.cost_entry);

frappe.ui.form.on("Shipping Cost Entry", {
	refresh(frm) {
		const ns = erpnext_shipping.cost_entry;
		if (!document.getElementById("sce-style")) $("head").append(ns.STYLE);
		const draw = (fieldname, html) => {
			const field = frm.get_field(fieldname);
			if (field && field.$wrapper) field.$wrapper.html(html);
		};
		draw("summary_html", frm.is_new() ? "" : ns.summary_html(frm.doc));
		draw("breakdown_html", ns.breakdown_html(frm.doc));
		draw("measure_html", ns.measure_html(frm.doc));

		if (frm.doc.dispute) {
			frm.add_custom_button(__("Dispute"), () => frappe.set_route("Form", "Carrier Invoice Dispute", frm.doc.dispute), __("Open"));
		}
		if (!frm.is_new() && ns.can_dispute(frm.doc)) {
			frm.add_custom_button(__("Dispute This Charge"), () => {
				frappe
					.call({
						method: "erpnext_shipping.erpnext_shipping.dispute.create_disputes",
						args: { cost_entries: [frm.doc.name] },
						freeze: true,
					})
					.then((r) => {
						const m = r.message || {};
						if ((m.disputes || []).length) frappe.set_route("Form", "Carrier Invoice Dispute", m.disputes[0]);
						else if ((m.skipped || []).length) frappe.msgprint(m.skipped[0].why);
					});
			});
		}
		if (frm.doc.shipment) {
			frm.add_custom_button(__("Shipment"), () => frappe.set_route("Form", "Shipment", frm.doc.shipment), __("Open"));
		}
		if (frm.doc.delivery_note) {
			frm.add_custom_button(
				__("Delivery Note"),
				() => frappe.set_route("Form", "Delivery Note", frm.doc.delivery_note),
				__("Open")
			);
		}
	},
});
