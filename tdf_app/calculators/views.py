from django.shortcuts import render

from . import engine
from .forms import BEDForm, TDFForm


def home(request):
    return render(request, "calculators/home.html")


def tdf_view(request):
    result = None
    error = None
    if request.method == "POST":
        form = TDFForm(request.POST)
        if form.is_valid():
            try:
                d = dict(form.cleaned_data)
                d["dose_per_fx_cgy"] = d.pop("dose_per_fx_gy") * 100.0     # formula works in cGy
                d["total_dose_cgy"] = d.pop("total_dose_gy") * 100.0
                result = engine.calculate_tdf(engine.TDFInput(**d))
            except (ValueError, ZeroDivisionError, OverflowError) as exc:
                error = str(exc)
    else:
        form = TDFForm()
    return render(request, "calculators/tdf.html",
                  {"form": form, "result": result, "error": error, "active": "tdf"})


def bed_view(request):
    result = None
    error = None
    scenarios = None
    selected_key = None
    if request.method == "POST":
        form = BEDForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            strategy, fx_per_day = BEDForm.parse_mode(d["mode"])
            inp = engine.BEDMakeupInput(
                rx_dose_per_fx_gy=d["rx_dose_per_fx_gy"], rx_total_dose_gy=d["rx_total_dose_gy"],
                first_tx=d["first_tx"], planned_last_tx=d["planned_last_tx"],
                last_tx_before_gap=d["last_tx_before_gap"], first_tx_after_gap=d["first_tx_after_gap"],
                pre_fx=d["pre_fx"],
                post_dose_per_fx_gy=d["rx_dose_per_fx_gy"],      # add-fractions keeps the Rx dose/fx
                fx_per_week=engine.DEFAULT_FX_PER_WEEK, fx_per_day=fx_per_day,
                tumor=engine.TissueParams(d["tumor_alpha_beta"], d["tumor_k"], d["tumor_tk"]),
                early=engine.TissueParams(d["early_alpha_beta"], d["early_k"], d["early_tk"]),
                late=engine.TissueParams(d["late_alpha_beta"], d["late_k"], d["late_tk"]),
            )
            try:
                if strategy == "dose":
                    result = engine.calculate_bed_dose_makeup(inp)
                else:
                    result = engine.calculate_bed_makeup(inp)
                scenarios = engine.compare_schedules(inp)
                selected_key = d["mode"]
            except (ValueError, ZeroDivisionError, OverflowError) as exc:
                error = str(exc)
    else:
        form = BEDForm()
    return render(request, "calculators/bed.html", {
        "form": form, "result": result, "error": error, "active": "bed",
        "mode": BEDForm.parse_mode(form["mode"].value())[0],
        "bid": BEDForm.parse_mode(form["mode"].value())[1] == 2,
        "scenarios": scenarios, "selected_key": selected_key,
        "presets": engine.TISSUE_PRESETS,
    })


def reference_view(request):
    return render(request, "calculators/reference.html", {
        "active": "reference",
        "model_parameters": engine.MODEL_PARAMETERS,
        "tolerance": engine.TISSUE_TOLERANCE,
    })


def rule_of_thumb_view(request):
    return render(request, "calculators/rule_of_thumb.html",
                  {"active": "rule", "rule": engine.RULE_OF_THUMB})
