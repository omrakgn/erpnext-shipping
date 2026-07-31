# DPD claim base forms

Put the **original blank** DPD "Declaration of Non-Receipt" PDFs here. The app
stamps the claim values onto these files so the printed/emailed form is
pixel-identical to the carrier's form (see `../loss_form.py`).

| File               | Region / language                     | Return address        |
| ------------------ | ------------------------------------- | --------------------- |
| `dpd_form_de.pdf`  | Germany — DE/EN                       | DPD NL, Oirschot      |
| `dpd_form_belux.pdf` | Belgium / NL / LU — NL/EN           | DPD BELUX, reply@dpd.be |

These are static app assets and are committed to the repo (pulled on deploy).
Do **not** rename them — the filenames are referenced by `FORM_FILES`.
