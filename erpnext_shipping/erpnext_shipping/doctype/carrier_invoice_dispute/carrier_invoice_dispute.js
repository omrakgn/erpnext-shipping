// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

// Kargo faturası itirazı: üstte durum, sıradaki adım ve tek düğme; altında kime, hangi
// fatura, ne kadar, hangi tarihler. Tasarım: docs/plans/kargo-maliyet-ekrani-ve-itiraz.md §3.
// Gönderi ekranıyla aynı kart (shipment_view.js). Çizim fonksiyonları saf: belge alır, HTML döner.

frappe.provide("erpnext_shipping.dispute");

(function (ns) {
	ns.METHOD = "erpnext_shipping.erpnext_shipping.dispute.";
	// Son güne bu kadar gün kala kart uyarır; sunucudaki hatırlatmayla aynı.
	ns.REMIND_BEFORE_DAYS = 3;

	ns.ACTION_LABELS = {
		send_email: "Send by Email",
		portal: "Copy Text for Portal",
		mark_sent: "Mark as Sent",
		answer: "Record Answer",
		debit_note: "Create Debit Note",
		open_invoice: "Open Purchase Invoice",
		open_debit_note: "Open Debit Note",
		settings: "Open Settings",
		close: "Close Dispute",
	};

	ns.STYLE = `<style id="cid-style">
		.cid-card { border: 1px solid var(--border-color, #d1d8dd); border-radius: var(--border-radius-md, 8px);
			padding: 12px 14px; background: var(--card-bg, #fff); margin-bottom: 4px; }
		.cid-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; }
		.cid-status.indicator-pill { font-size: var(--text-lg, 15px); height: auto; padding: 5px 12px; white-space: normal; line-height: 1.35; }
		.cid-actions { display: flex; flex-wrap: wrap; gap: 6px; }
		.cid-line { margin-top: 8px; color: var(--text-color, #1f272e); overflow-wrap: anywhere; }
		.cid-line .cid-k { color: var(--text-muted, #6c7680); margin-right: 6px; }
		.cid-num { font-variant-numeric: tabular-nums; font-weight: 500; white-space: nowrap; }
		.cid-pre { white-space: pre-wrap; font-family: var(--font-stack-monospace, monospace); font-size: var(--text-sm, 12px);
			background: var(--control-bg, #f4f5f6); border-radius: var(--border-radius, 6px); padding: 10px; max-height: 320px; overflow: auto; }
		@media (max-width: 576px) {
			.cid-actions { width: 100%; }
			.cid-actions .btn { flex: 1 1 100%; }
		}
	</style>`;

	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const money = (doc, v) => format_currency(flt(v), doc.currency || "EUR");
	const day = (d) => frappe.datetime.str_to_user(d);
	const pill = (label, color, cls) =>
		`<span class="indicator-pill ${color} ${cls || ""}">${esc(label)}</span>`;

	// Gün farkı, saat dilimine takılmadan (iki tarih de yyyy-mm-dd).
	ns.days_between = function (from, to) {
		const a = Date.UTC(
			...String(from)
				.slice(0, 10)
				.split("-")
				.map((x, i) => Number(x) - (i === 1 ? 1 : 0))
		);
		const b = Date.UTC(
			...String(to)
				.slice(0, 10)
				.split("-")
				.map((x, i) => Number(x) - (i === 1 ? 1 : 0))
		);
		return Math.round((b - a) / 86400000);
	};

	// Son gün: kaç gün kaldı, renk. Yalnız taslakta anlamlı.
	ns.deadline_info = function (doc, today) {
		if (!doc.deadline) return null;
		const left = ns.days_between(today, doc.deadline);
		if (left < 0)
			return { text: __("deadline passed on {0}", [day(doc.deadline)]), color: "red" };
		if (left === 0) return { text: __("file today"), color: "red" };
		const text =
			left === 1
				? __("file by {0}, 1 day left", [day(doc.deadline)])
				: __("file by {0}, {1} days left", [day(doc.deadline), left]);
		return { text: text, color: left <= ns.REMIND_BEFORE_DAYS ? "orange" : "gray" };
	};

	ns.state = function (doc, states, today) {
		states = states || {};
		const lines = doc.lines || [];
		const party = doc.billed_by || __("the carrier");
		const accepted = money(doc, doc.total_accepted);

		if (doc.status === "Draft") {
			if (!lines.length)
				return {
					color: "gray",
					text: __("Draft: add the charges you dispute"),
					actions: [],
				};
			if (lines.some((r) => !(flt(r.disputed_amount) > 0))) {
				return {
					color: "gray",
					text: __("Draft: enter the disputed amount on each line"),
					actions: [],
				};
			}
			const dl = ns.deadline_info(doc, today);
			const base = __("Ready to send to {0}", [party]);
			const text = dl ? `${base} · ${dl.text}` : base;
			const color = dl && dl.color !== "gray" ? dl.color : "blue";
			if (doc.method === "Portal") return { color, text, actions: ["portal", "mark_sent"] };
			if (doc.method === "Email" && doc.dispute_email)
				return { color, text, actions: ["send_email", "mark_sent"] };
			const note =
				doc.method === "Email"
					? __(
							"No dispute e-mail is set for {0}. Add it in Shipping Cost Settings, or send it yourself and mark it sent.",
							[party]
					  )
					: __(
							"Shipping Cost Settings does not say how {0} takes disputes. Add it under Invoice Disputes.",
							[party]
					  );
			return { color, text, note, actions: ["settings", "mark_sent"] };
		}

		if (doc.status === "Sent") {
			const sent = doc.sent_on ? __("Sent {0}", [day(doc.sent_on)]) : __("Sent");
			if (doc.answer_due && ns.days_between(today, doc.answer_due) < 0) {
				return {
					color: "orange",
					text: __("{0}, the answer from {1} was due {2}", [
						sent,
						party,
						day(doc.answer_due),
					]),
					actions: ["answer"],
				};
			}
			const due = doc.answer_due ? __(", answer due {0}", [day(doc.answer_due)]) : "";
			return {
				color: "blue",
				text: __("{0}, waiting for {1}", [sent, party]) + due,
				actions: ["answer"],
			};
		}

		if (doc.status === "Accepted" || doc.status === "Partly Accepted") {
			const color = doc.status === "Accepted" ? "green" : "orange";
			const head =
				doc.status === "Accepted"
					? __("Accepted {0}", [accepted])
					: __("Partly accepted: {0}", [accepted]);
			if (doc.debit_note && states.debit_note === 0) {
				return {
					color: "orange",
					text: __("{0}. Debit note {1} is a draft: submit it to finish.", [
						head,
						doc.debit_note,
					]),
					actions: ["open_debit_note"],
				};
			}
			if (doc.purchase_invoice && states.purchase_invoice === 1) {
				return {
					color,
					text: __("{0}. Book the carrier's credit.", [head]),
					actions: ["debit_note"],
				};
			}
			if (doc.purchase_invoice) {
				return {
					color: "orange",
					text: __("{0}. Submit Purchase Invoice {1}, then book the credit.", [
						head,
						doc.purchase_invoice,
					]),
					actions: ["open_invoice"],
				};
			}
			return {
				color,
				text: __(
					"{0}. No Purchase Invoice is linked: book the credit by hand, then close the dispute.",
					[head]
				),
				actions: ["close"],
			};
		}

		if (doc.status === "Rejected") {
			const when = doc.decided_on ? __(" on {0}", [day(doc.decided_on)]) : "";
			return { color: "red", text: __("Rejected by {0}", [party]) + when, actions: [] };
		}
		if (doc.status === "Credited") {
			const when = doc.credited_on ? __(" on {0}", [day(doc.credited_on)]) : "";
			return {
				color: "green",
				text: __("Credited {0}", [accepted]) + when,
				actions: doc.debit_note ? ["open_debit_note"] : [],
			};
		}
		return { color: "gray", text: __("Closed"), actions: [] };
	};

	const button = (action, primary) =>
		`<button class="btn btn-sm ${primary ? "btn-primary" : "btn-default"}" data-action="${esc(
			action
		)}">${esc(__(ns.ACTION_LABELS[action]))}</button>`;

	ns.summary_html = function (doc, states, today) {
		const st = ns.state(doc, states, today);
		const parts = [];
		const actions = st.actions.map((a, i) => button(a, i === 0)).join("");
		parts.push(
			`<div class="cid-head">${pill(st.text, st.color, "cid-status")}${
				actions ? `<div class="cid-actions">${actions}</div>` : ""
			}</div>`
		);
		if (st.note) parts.push(`<div class="cid-line text-muted">${esc(st.note)}</div>`);

		// Kime, hangi fatura.
		const who = [
			doc.billed_by,
			doc.invoice_number ? __("invoice {0}", [doc.invoice_number]) : null,
		];
		if (doc.invoice_date) who.push(day(doc.invoice_date));
		if (
			doc.carrier &&
			doc.carrier.toLowerCase() !== String(doc.billed_by || "").toLowerCase()
		) {
			who.push(__("carried by {0}", [doc.carrier]));
		}
		parts.push(`<div class="cid-line">${who.filter(Boolean).map(esc).join(" · ")}</div>`);

		// Ne kadar.
		const lines = doc.lines || [];
		const count = lines.length === 1 ? __("1 parcel") : __("{0} parcels", [lines.length]);
		let amounts = __("{0} disputed of {1} billed", [
			`<span class="cid-num">${esc(money(doc, doc.total_disputed))}</span>`,
			`<span class="cid-num">${esc(money(doc, doc.total_billed))}</span>`,
		]);
		if (doc.decision) {
			amounts += ` · ${__("{0} accepted", [
				`<span class="cid-num">${esc(money(doc, doc.total_accepted))}</span>`,
			])}`;
		}
		parts.push(`<div class="cid-line">${esc(count)} · ${amounts}</div>`);

		// Tarihler ve taşıyıcının verdiği numaralar.
		const facts = [];
		if (doc.status === "Draft" && doc.deadline) facts.push([__("File by"), day(doc.deadline)]);
		if (doc.sent_on) facts.push([__("Sent"), day(doc.sent_on)]);
		if (doc.status === "Sent" && doc.answer_due)
			facts.push([__("Answer due"), day(doc.answer_due)]);
		if (doc.portal_reference) facts.push([__("Carrier reference"), doc.portal_reference]);
		if (doc.carrier_credit_note)
			facts.push([__("Carrier credit note"), doc.carrier_credit_note]);
		if (facts.length) {
			parts.push(
				`<div class="cid-line">${facts
					.map(([k, v]) => `<span class="cid-k">${esc(k)}</span>${esc(v)}`)
					.join(" · ")}</div>`
			);
		}
		return `${ns.STYLE}<div class="cid-card">${parts.join("")}</div>`;
	};

	// Araç çubuğunda hangi işler: durumun izin verdikleri.
	ns.toolbar_actions = function (doc, states) {
		states = states || {};
		const live_note = doc.debit_note && (states.debit_note === 0 || states.debit_note === 1);
		const out = [];
		if (doc.status === "Draft" || doc.status === "Sent") out.push("send_email", "portal");
		if (doc.status === "Draft") out.push("mark_sent");
		if (["Sent", "Accepted", "Partly Accepted", "Rejected"].includes(doc.status) && !live_note)
			out.push("answer");
		if (
			["Accepted", "Partly Accepted"].includes(doc.status) &&
			!live_note &&
			doc.purchase_invoice
		) {
			out.push("debit_note");
		}
		if (!["Credited", "Closed"].includes(doc.status)) out.push("close");
		return out;
	};
})(erpnext_shipping.dispute);

frappe.ui.form.on("Carrier Invoice Dispute", {
	setup(frm) {
		// Satıra yalnız bu faturanın kayıtları gelsin; faturalayan sunucuda denetleniyor.
		frm.set_query("cost_entry", "lines", () => ({
			filters: frm.doc.invoice_number ? { invoice_number: frm.doc.invoice_number } : {},
		}));
	},

	refresh(frm) {
		const ns = erpnext_shipping.dispute;
		const states = (frm.doc.__onload || {}).states || {};
		const field = frm.get_field("summary_html");
		if (field && field.$wrapper) {
			field.$wrapper.html(
				frm.is_new() ? "" : ns.summary_html(frm.doc, states, frappe.datetime.get_today())
			);
			field.$wrapper.off("click.cid").on("click.cid", "[data-action]", (e) => {
				e.preventDefault();
				run_dispute_action(frm, $(e.currentTarget).attr("data-action"));
			});
		}
		if (frm.is_new()) return;

		for (const action of ns.toolbar_actions(frm.doc, states)) {
			frm.add_custom_button(
				__(ns.ACTION_LABELS[action]),
				() => run_dispute_action(frm, action),
				__("Dispute")
			);
		}
		if (frm.doc.purchase_invoice) {
			frm.add_custom_button(
				__("Purchase Invoice"),
				() => frappe.set_route("Form", "Purchase Invoice", frm.doc.purchase_invoice),
				__("Open")
			);
		}
		if (frm.doc.debit_note) {
			frm.add_custom_button(
				__("Debit Note"),
				() => frappe.set_route("Form", "Purchase Invoice", frm.doc.debit_note),
				__("Open")
			);
		}
	},
});

function run_dispute_action(frm, action) {
	const ns = erpnext_shipping.dispute;
	const call = (method, args) =>
		frappe.call({
			method: ns.METHOD + method,
			args: Object.assign({ dispute: frm.doc.name }, args || {}),
			freeze: true,
		});

	// Kaydedilmemiş değişiklik gönderilen metne girmez: önce kaydet.
	const after_save = (fn) => (frm.is_dirty() ? frm.save().then(fn) : fn());

	if (action === "send_email") {
		after_save(() =>
			call("email_draft").then((r) => {
				if (!r.message) return;
				const composer = new frappe.views.CommunicationComposer({
					doc: frm.doc,
					frm: frm,
					recipients: r.message.recipients,
					subject: r.message.subject,
					message: r.message.message,
					attach_document_print: false,
				});
				// Giden e-posta itirazı sunucuda "Sent" yapıyor; pencere kapanınca kartı tazele.
				composer.dialog.$wrapper.one("hidden.bs.modal", () => frm.reload_doc());
			})
		);
	} else if (action === "portal") {
		after_save(() =>
			call("portal_text").then((r) => r.message && portal_dialog(frm, r.message))
		);
	} else if (action === "mark_sent") {
		after_save(() => mark_sent_dialog(frm));
	} else if (action === "answer") {
		after_save(() => answer_dialog(frm));
	} else if (action === "debit_note") {
		frappe.confirm(
			__("Create a draft debit note for {0} against Purchase Invoice {1}?", [
				format_currency(frm.doc.total_accepted, frm.doc.currency),
				frm.doc.purchase_invoice,
			]),
			() =>
				call("make_debit_note").then((r) => {
					if (r.message) frappe.set_route("Form", "Purchase Invoice", r.message);
				})
		);
	} else if (action === "open_invoice") {
		frappe.set_route("Form", "Purchase Invoice", frm.doc.purchase_invoice);
	} else if (action === "open_debit_note") {
		frappe.set_route("Form", "Purchase Invoice", frm.doc.debit_note);
	} else if (action === "settings") {
		frappe.set_route("Form", "Shipping Cost Settings");
	} else if (action === "close") {
		frappe.prompt(
			{ fieldname: "note", fieldtype: "Small Text", label: __("Why"), reqd: 1 },
			(v) => call("close", { note: v.note }).then(() => frm.reload_doc()),
			__("Close Dispute"),
			__("Close")
		);
	}
}

function portal_dialog(frm, data) {
	const d = new frappe.ui.Dialog({
		title: __("Copy Text for Portal"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "how",
				options: `<p>${frappe.utils.escape_html(
					__(
						"Copy the text, paste it into the portal and attach the invoice. Then note the reference the portal gives."
					)
				)}</p>${
					data.portal_url
						? `<p><a href="${frappe.utils.escape_html(
								data.portal_url
						  )}" target="_blank" rel="noopener">${frappe.utils.escape_html(
								__("Open the portal")
						  )}</a></p>`
						: ""
				}<div class="cid-pre">${frappe.utils.escape_html(data.text)}</div>`,
			},
			{ fieldtype: "Data", fieldname: "portal_reference", label: __("Carrier Reference") },
		],
		primary_action_label: __("Mark as Sent"),
		primary_action(values) {
			frappe
				.call({
					method: erpnext_shipping.dispute.METHOD + "mark_sent",
					args: {
						dispute: frm.doc.name,
						method: "Portal",
						portal_reference: values.portal_reference,
					},
					freeze: true,
				})
				.then(() => {
					d.hide();
					frm.reload_doc();
				});
		},
		secondary_action_label: __("Copy Text"),
		secondary_action() {
			frappe.utils.copy_to_clipboard(data.text);
		},
	});
	d.show();
}

function mark_sent_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Mark as Sent"),
		fields: [
			{
				fieldtype: "Date",
				fieldname: "sent_on",
				label: __("Sent On"),
				default: frappe.datetime.get_today(),
				reqd: 1,
			},
			{
				fieldtype: "Select",
				fieldname: "method",
				label: __("Sent By"),
				options: ["Email", "Portal"],
				default: frm.doc.method || "Email",
			},
			{ fieldtype: "Data", fieldname: "portal_reference", label: __("Carrier Reference") },
		],
		primary_action_label: __("Mark as Sent"),
		primary_action(values) {
			frappe
				.call({
					method: erpnext_shipping.dispute.METHOD + "mark_sent",
					args: Object.assign({ dispute: frm.doc.name }, values),
					freeze: true,
				})
				.then(() => {
					d.hide();
					frm.reload_doc();
				});
		},
	});
	d.show();
}

function answer_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Record Answer"),
		fields: [
			{
				fieldtype: "Select",
				fieldname: "decision",
				label: __("Answer"),
				options: [
					{ value: "Accepted", label: __("Accepted in full") },
					{ value: "Partly Accepted", label: __("Partly accepted") },
					{ value: "Rejected", label: __("Rejected") },
				],
				default: "Accepted",
				reqd: 1,
			},
			{
				fieldtype: "Currency",
				fieldname: "accepted_amount",
				label: __("Accepted Amount"),
				depends_on: "eval:doc.decision == 'Partly Accepted'",
				description: __(
					"Of {0} disputed. Split over the lines in proportion; leave empty to keep the amounts typed on the lines.",
					[format_currency(frm.doc.total_disputed, frm.doc.currency)]
				),
			},
			{
				fieldtype: "Data",
				fieldname: "carrier_credit_note",
				label: __("Carrier Credit Note"),
				depends_on: "eval:doc.decision != 'Rejected'",
				default: frm.doc.carrier_credit_note,
			},
			{
				fieldtype: "Date",
				fieldname: "decided_on",
				label: __("Answered On"),
				default: frappe.datetime.get_today(),
			},
			{ fieldtype: "Small Text", fieldname: "notes", label: __("Note") },
		],
		primary_action_label: __("Save Answer"),
		primary_action(values) {
			frappe
				.call({
					method: erpnext_shipping.dispute.METHOD + "record_answer",
					args: Object.assign({ dispute: frm.doc.name }, values),
					freeze: true,
				})
				.then(() => {
					d.hide();
					frm.reload_doc();
				});
		},
	});
	d.show();
}
