from django import forms

from .engine import DEFAULT_EARLY, DEFAULT_LATE, DEFAULT_TUMOR


class DateInput(forms.DateInput):
    input_type = "date"


def _num(label, initial=None, step="any", min_value=0, help_text="", required=True):
    return forms.FloatField(
        label=label, initial=initial, min_value=min_value, required=required,
        help_text=help_text, widget=forms.NumberInput(attrs={"step": step}),
    )


def _int(label, initial=None, min_value=0, help_text="", required=True):
    return forms.IntegerField(
        label=label, initial=initial, min_value=min_value, required=required,
        help_text=help_text,
    )


def _date(label, help_text=""):
    return forms.DateField(label=label, help_text=help_text, widget=DateInput())


class TDFForm(forms.Form):
    # Initial prescription
    dose_per_fx_cgy = _num("Dose per fraction (cGy)", 200, step="1")
    total_dose_cgy = _num("Total dose (cGy)", 6000, step="1")
    fx_per_day = _int("Fractions per day", 1, min_value=1)
    fx_per_week = _int("Fractions per week", 5, min_value=1)

    # Dates
    start_date = _date("Start date (first fraction)")
    original_end_date = _date("Original planned end date")
    last_tx_before_break = _date("Date of last treatment before break")
    first_tx_after_break = _date("Date of first treatment after break")
    proposed_finish_date = _date("Proposed finish date")

    # Treatment before break
    fx_treated_before_break = _int("Fractions treated before break", min_value=1)

    def clean(self):
        c = super().clean()
        needed = ["start_date", "original_end_date", "last_tx_before_break",
                  "first_tx_after_break", "proposed_finish_date"]
        if all(c.get(k) for k in needed):
            if c["original_end_date"] <= c["start_date"]:
                self.add_error("original_end_date", "Must be after the start date.")
            if c["last_tx_before_break"] <= c["start_date"]:
                self.add_error("last_tx_before_break", "Must be after the start date.")
            if c["first_tx_after_break"] <= c["last_tx_before_break"]:
                self.add_error("first_tx_after_break", "Must be after the last treatment before the break.")
            if c["proposed_finish_date"] <= c["first_tx_after_break"]:
                self.add_error("proposed_finish_date", "Must be after the first treatment after the break.")
        if c.get("dose_per_fx_cgy") and c.get("total_dose_cgy") and c.get("fx_treated_before_break"):
            total_fx = c["total_dose_cgy"] / c["dose_per_fx_cgy"]
            if c["fx_treated_before_break"] >= total_fx:
                self.add_error("fx_treated_before_break",
                               f"Must be fewer than the total fraction count ({total_fx:g}).")
        return c


class BEDForm(forms.Form):
    # Original prescription
    rx_dose_per_fx_gy = _num("Dose per fraction (Gy)", 2, step="0.01")
    rx_total_dose_gy = _num("Total dose (Gy)", 50, step="0.01")

    # Dates
    first_tx = _date("Date of first treatment")
    planned_last_tx = _date("Date of last treatment (as prescribed)")
    last_tx_before_gap = _date("Date of last treatment before gap")
    first_tx_after_gap = _date("Date of first treatment after gap")
    proposed_final_tx = _date("Proposed final day of treatment")

    # Pre-gap delivered
    pre_dose_per_fx_gy = _num("Dose per fraction (Gy)", 2, step="0.01")
    pre_fx = _int("Fractions delivered", 20, min_value=1)

    # Post-gap proposed
    post_dose_per_fx_gy = _num("Dose per fraction (Gy)", 2, step="0.01")
    post_fx = _int("Fractions proposed", 5, min_value=0)

    # Tissue parameters
    tumor_alpha_beta = _num("α/β (Gy)", DEFAULT_TUMOR.alpha_beta, step="0.1", min_value=0.1)
    tumor_k = _num("K (Gy/day)", DEFAULT_TUMOR.k, step="0.01")
    tumor_tk = _num("Tk (days)", DEFAULT_TUMOR.tk, step="1")

    early_alpha_beta = _num("α/β (Gy)", DEFAULT_EARLY.alpha_beta, step="0.1", min_value=0.1)
    early_k = _num("K (Gy/day)", DEFAULT_EARLY.k, step="0.01")
    early_tk = _num("Tk (days)", DEFAULT_EARLY.tk, step="1")

    late_alpha_beta = _num("α/β (Gy)", DEFAULT_LATE.alpha_beta, step="0.1", min_value=0.1)
    late_k = _num("K (Gy/day)", DEFAULT_LATE.k, step="0.01")
    late_tk = _num("Tk (days)", DEFAULT_LATE.tk, step="1")

    PARAM_FIELDS = ("tumor_alpha_beta", "tumor_k", "tumor_tk", "early_alpha_beta", "early_k",
                    "early_tk", "late_alpha_beta", "late_k", "late_tk")

    def param_errors(self):
        out = []
        for name in self.PARAM_FIELDS:
            for e in self[name].errors:
                label = name.replace("_", " ").replace("alpha beta", "α/β").capitalize()
                out.append((label, e))
        return out

    def clean(self):
        c = super().clean()
        needed = ["first_tx", "planned_last_tx", "last_tx_before_gap",
                  "first_tx_after_gap", "proposed_final_tx"]
        if all(c.get(k) for k in needed):
            if c["planned_last_tx"] <= c["first_tx"]:
                self.add_error("planned_last_tx", "Must be after the first treatment.")
            if c["last_tx_before_gap"] < c["first_tx"]:
                self.add_error("last_tx_before_gap", "Cannot be before the first treatment.")
            if c["first_tx_after_gap"] <= c["last_tx_before_gap"]:
                self.add_error("first_tx_after_gap", "Must be after the last treatment before the gap.")
            if c["proposed_final_tx"] < c["first_tx_after_gap"]:
                self.add_error("proposed_final_tx", "Cannot be before the first treatment after the gap.")
        return c
