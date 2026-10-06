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
                result = engine.calculate_tdf(engine.TDFInput(**form.cleaned_data))
            except (ValueError, ZeroDivisionError, OverflowError) as exc:
                error = str(exc)
    else:
        form = TDFForm()
    return render(request, "calculators/tdf.html",
                  {"form": form, "result": result, "error": error, "active": "tdf"})


def bed_view(request):
    result = None
    error = None
    if request.method == "POST":
        form = BEDForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            inp = engine.BEDInput(
                rx_dose_per_fx_gy=d["rx_dose_per_fx_gy"], rx_total_dose_gy=d["rx_total_dose_gy"],
                first_tx=d["first_tx"], planned_last_tx=d["planned_last_tx"],
                last_tx_before_gap=d["last_tx_before_gap"], first_tx_after_gap=d["first_tx_after_gap"],
                proposed_final_tx=d["proposed_final_tx"],
                pre_dose_per_fx_gy=d["pre_dose_per_fx_gy"], pre_fx=d["pre_fx"],
                post_dose_per_fx_gy=d["post_dose_per_fx_gy"], post_fx=d["post_fx"],
                tumor=engine.TissueParams(d["tumor_alpha_beta"], d["tumor_k"], d["tumor_tk"]),
                early=engine.TissueParams(d["early_alpha_beta"], d["early_k"], d["early_tk"]),
                late=engine.TissueParams(d["late_alpha_beta"], d["late_k"], d["late_tk"]),
            )
            try:
                result = engine.calculate_bed(inp)
            except (ValueError, ZeroDivisionError, OverflowError) as exc:
                error = str(exc)
    else:
        form = BEDForm()
    return render(request, "calculators/bed.html", {
        "form": form, "result": result, "error": error, "active": "bed",
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
