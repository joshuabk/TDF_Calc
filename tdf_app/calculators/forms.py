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
    # Entered in Gy; the TDF formula itself takes cGy, so the view converts.
    dose_per_fx_gy = _num("Dose per fraction (Gy)", 2, step="0.01")
    total_dose_gy = _num("Total dose (Gy)", 60, step="0.01")
    fx_per_day = _int("Fractions per day", 1, min_value=1)
    fx_per_week = _int("Fractions per week", 5, min_value=1)

    # Dates.  The planned end date is derived (last prescribed fraction) and the
    # finish date is solved for, so neither is typed.
    start_date = _date("Start date (first fraction)")
    original_end_date = forms.DateField(
        label="Original planned end date", required=False,
        help_text="Filled in automatically: the last prescribed fraction at the fractions per week / day above.",
        widget=DateInput(attrs={"readonly": "readonly", "tabindex": "-1"}))
    last_tx_before_break = _date("Date of last treatment before break")
    first_tx_after_break = _date("Date of first treatment after break")

    # Treatment before break: counted from the calendar unless overridden
    fx_treated_before_break = _int("Fractions treated before break (optional)", None, min_value=1, required=False,
                                   help_text="Leave blank to count treatment days from the start date through the "
                                             "last treatment before the break. Enter it if days were missed.")

    def clean(self):
        c = super().clean()
        from .engine import nth_treatment_date
        c["original_end_date"] = None
        if c.get("dose_per_fx_gy") and c.get("total_dose_gy") and c.get("start_date") \
                and c.get("fx_per_week") and c.get("fx_per_day"):
            total_fx = c["total_dose_gy"] / c["dose_per_fx_gy"]
            if abs(total_fx - round(total_fx)) < 1e-9 and round(total_fx) >= 1:
                c["original_end_date"] = nth_treatment_date(
                    c["start_date"], int(round(total_fx)), c["fx_per_week"], c["fx_per_day"])
            else:
                self.add_error("total_dose_gy",
                               f"Total dose / dose per fraction = {total_fx:.2f}; must be a whole number of fractions.")
        needed = ["start_date", "last_tx_before_break", "first_tx_after_break"]
        if all(c.get(k) for k in needed):
            if c["last_tx_before_break"] <= c["start_date"]:
                self.add_error("last_tx_before_break", "Must be after the start date.")
            if c["first_tx_after_break"] <= c["last_tx_before_break"]:
                self.add_error("first_tx_after_break", "Must be after the last treatment before the break.")
        if c.get("dose_per_fx_gy") and c.get("total_dose_gy") and c.get("fx_treated_before_break") is not None:
            total_fx = c["total_dose_gy"] / c["dose_per_fx_gy"]
            if c["fx_treated_before_break"] >= total_fx:
                self.add_error("fx_treated_before_break",
                               f"Must be fewer than the total fraction count ({total_fx:g}).")
        return c


class BEDForm(forms.Form):
    # value = "<strategy>-<fx per day>"; parsed by strategy() / fx_per_day()
    MODE_CHOICES = [
        ("fractions-1", "Add fractions — keep the dose per fraction, extend the course"),
        ("dose-1", "Increase dose per fraction — keep the remaining fractions, no extra treatment days"),
        ("dose-2", "BID — keep the remaining fractions, 2 fx/day (≥ 6 h apart), dose per fraction solved"),
    ]
    mode = forms.ChoiceField(label="Calculation", choices=MODE_CHOICES, initial="fractions-1")

    @staticmethod
    def parse_mode(value):
        """'fractions-2' -> ('fractions', 2)."""
        strategy, _, per_day = (value or "fractions-1").partition("-")
        return strategy, int(per_day or 1)

    # Original prescription
    rx_dose_per_fx_gy = _num("Dose per fraction (Gy)", 2, step="0.01")
    rx_total_dose_gy = _num("Total dose (Gy)", 50, step="0.01")

    # Dates
    first_tx = _date("Date of first treatment")
    planned_last_tx = forms.DateField(
        label="Date of last treatment (as prescribed)", required=False,
        help_text="Filled in automatically: the prescribed number of fractions, Mon–Fri, from the first treatment.",
        widget=DateInput(attrs={"readonly": "readonly", "tabindex": "-1"}))
    last_tx_before_gap = _date("Date of last treatment before gap")
    first_tx_after_gap = _date("Date of first treatment after gap")

    # Pre-gap delivered: counted from the calendar unless overridden
    pre_fx = _int("Fractions delivered before gap (optional)", None, min_value=1, required=False,
                  help_text="Leave blank to count treatment days from the first treatment through the "
                            "last treatment before the gap. Enter it if days were missed (holidays, "
                            "machine down).")

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
        from .engine import DEFAULT_FX_PER_WEEK, nth_treatment_date
        needed = ["first_tx", "last_tx_before_gap", "first_tx_after_gap"]
        for k in needed:
            if c.get(k) and c[k].weekday() >= 5:
                self.add_error(k, f"{c[k]:%A} — patients are not treated on weekends. Enter a weekday.")
        # The planned last treatment is derived, never typed: the prescribed
        # number of fractions, Mon-Fri, counted from the first treatment.
        c["planned_last_tx"] = None
        if c.get("rx_dose_per_fx_gy") and c.get("rx_total_dose_gy") and c.get("first_tx"):
            rx_fx = c["rx_total_dose_gy"] / c["rx_dose_per_fx_gy"]
            if abs(rx_fx - round(rx_fx)) < 1e-9 and round(rx_fx) >= 1:
                c["planned_last_tx"] = nth_treatment_date(c["first_tx"], int(round(rx_fx)), DEFAULT_FX_PER_WEEK)
            else:
                self.add_error("rx_total_dose_gy",
                               f"Total dose / dose per fraction = {rx_fx:.2f}; must be a whole number of fractions.")
        if all(c.get(k) for k in needed):
            if c["last_tx_before_gap"] < c["first_tx"]:
                self.add_error("last_tx_before_gap", "Cannot be before the first treatment.")
            if c["first_tx_after_gap"] <= c["last_tx_before_gap"]:
                self.add_error("first_tx_after_gap", "Must be after the last treatment before the gap.")
        if c.get("rx_dose_per_fx_gy") and c.get("rx_total_dose_gy") and c.get("pre_fx") is not None:
            rx_fx = c["rx_total_dose_gy"] / c["rx_dose_per_fx_gy"]
            if c["pre_fx"] >= rx_fx:
                self.add_error("pre_fx",
                               f"Must be fewer than the prescribed fraction count ({rx_fx:g}).")
        return c
