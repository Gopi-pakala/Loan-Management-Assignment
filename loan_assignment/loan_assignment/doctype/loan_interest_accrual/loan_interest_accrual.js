frappe.ui.form.on("Loan Interest Accrual", {
	loan(frm) {
		frm.trigger("fetch_interest");
	},
	period_start(frm) {
		frm.trigger("fetch_interest");
	},
	period_end(frm) {
		frm.trigger("fetch_interest");
	},
	fetch_interest(frm) {
		if (frm.doc.docstatus !== 0 || !(frm.doc.loan && frm.doc.period_start && frm.doc.period_end)) {
			return;
		}
		frappe
			.call({
				method: "loan_assignment.loan_assignment.doctype.loan_interest_accrual.loan_interest_accrual.get_scheduled_interest",
				args: {
					loan: frm.doc.loan,
					period_start: frm.doc.period_start,
					period_end: frm.doc.period_end,
				},
			})
			.then((r) => {
				if (r.message) {
					frm.set_value("interest_amount", r.message);
				} else {
					frappe.show_alert({
						message: __("No instalment of {0} falls due in this period. Enter the Interest Amount manually.", [frm.doc.loan]),
						indicator: "orange",
					});
				}
			});
	},
});
