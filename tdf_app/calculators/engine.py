"""
Radiobiological calculation engine for treatment-interruption compensation.

Every formula here is a direct port of the departmental Excel worksheet
("Treatment Interruption Worksheet.xlsx") so that results are identical to the
spreadsheet the department has been using.

Two models are implemented:

1. TDF (Time-Dose-Fractionation, Orton & Ellis)
       TDF = N * d^1.538 * (T/N)^-0.169 * 1e-3     (d in cGy, T in days)
   with the decay (rest-period) correction
       TDF_decayed = TDF1 * (T1 / (T1 + R))^0.11

2. BED (Linear-Quadratic with repopulation)
       BED = N * d * (1 + d / (alpha/beta)) - K * max(0, T - Tk)     (Gy)
"""

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional


# --------------------------------------------------------------------------- #
#  TDF model
# --------------------------------------------------------------------------- #

TDF_DOSE_EXP = 1.538
TDF_TIME_EXP = 0.169
TDF_DECAY_EXP = 0.11


def tdf(n_fx: float, dose_per_fx_cgy: float, elapsed_days: float) -> float:
    """TDF = N * d^1.538 * (T/N)^-0.169 * 10^-3  (spreadsheet cells F24, D33)."""
    if n_fx <= 0:
        return 0.0
    t_over_n = elapsed_days / n_fx
    if t_over_n <= 0:
        raise ValueError("Elapsed days must be greater than zero.")
    return n_fx * dose_per_fx_cgy ** TDF_DOSE_EXP * t_over_n ** (-TDF_TIME_EXP) * 1e-3


def tdf_decay(tdf1: float, t1_days: float, rest_days: float) -> float:
    """TDF after a rest period: TDF1 * (T1 / (T1 + R))^0.11  (cell D34)."""
    if t1_days <= 0:
        raise ValueError("Initial-phase elapsed days must be greater than zero.")
    return tdf1 * (t1_days / (t1_days + rest_days)) ** TDF_DECAY_EXP


def tdf_fractions_for(tdf_target: float, dose_per_fx_cgy: float, days: float) -> float:
    """
    Invert the TDF equation for N given a target TDF, dose/fx and overall time.

    Spreadsheet D38 writes this as
        (2^(3/1.169) * 5^(3/1.169) * T^(0.169/1.169) * TDF^(1/1.169)) / d^(1.538/1.169)
    which is algebraically  (TDF * 1e3 * T^0.169 / d^1.538) ^ (1/1.169).
    """
    if tdf_target <= 0:
        return 0.0
    if days <= 0:
        raise ValueError("Days available for the remaining phase must be greater than zero.")
    exp = 1.0 / (1.0 + TDF_TIME_EXP)  # 1/1.169
    return (tdf_target * 1e3 * days ** TDF_TIME_EXP / dose_per_fx_cgy ** TDF_DOSE_EXP) ** exp


@dataclass
class TDFInput:
    dose_per_fx_cgy: float          # C22 / B31
    total_dose_cgy: float           # D22
    start_date: date                # I30  Start date (first fraction)
    original_end_date: date         # I31  Original planned end date
    last_tx_before_break: date      # I32
    first_tx_after_break: date      # I33
    proposed_finish_date: date      # I36
    fx_treated_before_break: int    # D31
    fx_per_day: int = 1             # E22 (informational)
    fx_per_week: int = 5            # F22 (informational)


@dataclass
class TDFResult:
    total_fx: float                 # G22
    original_elapsed_days: int      # I31 - I30
    initial_phase_days: int         # I34
    rest_days: int                  # I35
    post_break_days: int            # I36 - I33
    tdf_total: float                # F24
    tdf1: float                     # D33
    tdf_arp: float                  # D34 (TDF after rest period)
    tdf2: float                     # D35 (TDF still needed)
    fx_remaining_planned: float     # G22 - D31
    fx_required_after_break: float  # N2 from inverse TDF
    fractions_to_add: float         # D38
    fractions_to_add_rounded: int
    tdf_percent_recovered: float    # tdf_arp / tdf_total * 100
    warnings: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def calculate_tdf(inp: TDFInput) -> TDFResult:
    warnings = []
    total_fx = inp.total_dose_cgy / inp.dose_per_fx_cgy
    if abs(total_fx - round(total_fx)) > 1e-9:
        warnings.append(
            f"Total dose / dose per fraction = {total_fx:.2f}, which is not a whole "
            "number of fractions. Check the prescription."
        )

    original_days = (inp.original_end_date - inp.start_date).days
    t1 = (inp.last_tx_before_break - inp.start_date).days
    rest = (inp.first_tx_after_break - inp.last_tx_before_break).days
    t2 = (inp.proposed_finish_date - inp.first_tx_after_break).days

    if original_days <= 0:
        raise ValueError("Original end date must be after the start date.")
    if t1 <= 0:
        raise ValueError("Date of last treatment before the break must be after the start date.")
    if rest <= 0:
        raise ValueError("First treatment after the break must be after the last treatment before it.")
    if t2 <= 0:
        raise ValueError("Proposed finish date must be after the first treatment after the break.")
    if inp.fx_treated_before_break <= 0 or inp.fx_treated_before_break >= total_fx:
        raise ValueError("Fractions treated before the break must be between 1 and the total fraction count.")

    tdf_total = tdf(total_fx, inp.dose_per_fx_cgy, original_days)
    tdf1 = tdf(inp.fx_treated_before_break, inp.dose_per_fx_cgy, t1)
    tdf_arp = tdf_decay(tdf1, t1, rest)
    tdf2 = tdf_total - tdf_arp

    fx_remaining = total_fx - inp.fx_treated_before_break
    n2 = tdf_fractions_for(tdf2, inp.dose_per_fx_cgy, t2)
    to_add = n2 - fx_remaining

    if to_add < 0:
        warnings.append(
            "Negative result: the proposed schedule delivers more TDF than the original "
            "prescription. No additional fractions are required."
        )

    return TDFResult(
        total_fx=total_fx,
        original_elapsed_days=original_days,
        initial_phase_days=t1,
        rest_days=rest,
        post_break_days=t2,
        tdf_total=tdf_total,
        tdf1=tdf1,
        tdf_arp=tdf_arp,
        tdf2=tdf2,
        fx_remaining_planned=fx_remaining,
        fx_required_after_break=n2,
        fractions_to_add=to_add,
        fractions_to_add_rounded=int(round(to_add)) if to_add > 0 else 0,
        tdf_percent_recovered=(tdf_arp / tdf_total * 100) if tdf_total else 0.0,
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
#  BED / Linear-Quadratic model
# --------------------------------------------------------------------------- #

@dataclass
class TissueParams:
    """alpha/beta (Gy), K = daily BED-equivalent of repopulation (Gy/day), Tk = kick-off (days)."""
    alpha_beta: float
    k: float = 0.0
    tk: float = 0.0


# Spreadsheet defaults (BED sheet rows 17-19)
DEFAULT_TUMOR = TissueParams(alpha_beta=3.0, k=0.6, tk=28)
DEFAULT_EARLY = TissueParams(alpha_beta=10.0, k=0.25, tk=0)
DEFAULT_LATE = TissueParams(alpha_beta=2.5, k=0.0, tk=0)

# Presets from the Alpha-Beta reference sheet
TISSUE_PRESETS = {
    "tumor": [
        {"label": "Rapidly growing tumor (default)", "alpha_beta": 10, "k": 0.6, "tk": 28},
        {"label": "Generic tumor (worksheet default)", "alpha_beta": 3, "k": 0.6, "tk": 28},
        {"label": "Slow growing tumor (e.g. prostate)", "alpha_beta": 1.5, "k": 0.1, "tk": 0},
    ],
    "early": [
        {"label": "Early-responding normal tissue", "alpha_beta": 10, "k": 0.25, "tk": 0},
    ],
    "late": [
        {"label": "Late-responding normal tissue", "alpha_beta": 2.5, "k": 0.0, "tk": 0},
    ],
}


def bed(n_fx: float, dose_per_fx_gy: float, tissue: TissueParams,
        overall_days: float = 0.0, apply_tk: bool = True) -> float:
    """
    BED = N d (1 + d/(a/b)) - K * (T - Tk)

    `apply_tk=True` reproduces the spreadsheet's IF(T < Tk, no repopulation term, ...)
    behaviour (used for tumor and for the first-phase/pre-gap terms).
    `apply_tk=False` reproduces the normal-tissue cells that subtract K*(T - Tk)
    unconditionally (Tk is 0 for those tissues in practice, so it is equivalent).
    """
    lq = n_fx * dose_per_fx_gy * (1.0 + dose_per_fx_gy / tissue.alpha_beta)
    if tissue.k == 0:
        return lq
    if apply_tk and overall_days < tissue.tk:
        return lq
    return lq - tissue.k * (overall_days - tissue.tk)


@dataclass
class BEDInput:
    # Original prescription
    rx_dose_per_fx_gy: float        # C24
    rx_total_dose_gy: float         # D24
    # Dates
    first_tx: date                  # F33
    planned_last_tx: date           # F34
    last_tx_before_gap: date        # F35
    first_tx_after_gap: date        # F36
    proposed_final_tx: date         # F37
    # Pre-gap delivered
    pre_dose_per_fx_gy: float       # A42
    pre_fx: int                     # C42
    # Post-gap proposed
    post_dose_per_fx_gy: float      # F42
    post_fx: int                    # H42
    # Tissue parameters
    tumor: TissueParams = field(default_factory=lambda: DEFAULT_TUMOR)
    early: TissueParams = field(default_factory=lambda: DEFAULT_EARLY)
    late: TissueParams = field(default_factory=lambda: DEFAULT_LATE)


@dataclass
class TissueBED:
    name: str
    alpha_beta: float
    k: float
    tk: float
    prescribed: float       # F26-F28
    before_gap: float       # C46-C48 (at date of last tx before gap)
    before_gap_at_resume: float  # D46-D48 (pre-gap dose re-evaluated at resumption, i.e. with gap-time repopulation)
    after_gap: float        # F46-F48
    total: float            # H46-H48
    percent_of_rx: float    # I46-I48
    delta: float            # total - prescribed


@dataclass
class BEDResult:
    rx_fx: float
    rx_calendar_days: int
    pre_total_dose: float
    pre_calendar_days: int
    gap_days: int
    post_total_dose: float
    post_calendar_days: int
    total_days: int
    total_dose: float
    tumor: TissueBED
    early: TissueBED
    late: TissueBED
    summary: str
    warnings: list = field(default_factory=list)

    @property
    def tissues(self):
        return [self.tumor, self.early, self.late]

    def as_dict(self):
        return asdict(self)


def calculate_bed(inp: BEDInput) -> BEDResult:
    warnings = []
    rx_fx = inp.rx_total_dose_gy / inp.rx_dose_per_fx_gy
    if abs(rx_fx - round(rx_fx)) > 1e-9:
        warnings.append(
            f"Prescription total / dose per fraction = {rx_fx:.2f}, which is not a whole "
            "number of fractions."
        )

    rx_days = (inp.planned_last_tx - inp.first_tx).days          # F24
    pre_days = (inp.last_tx_before_gap - inp.first_tx).days      # D42
    resume_days = (inp.first_tx_after_gap - inp.first_tx).days   # F36-F33
    gap_days = (inp.first_tx_after_gap - inp.last_tx_before_gap).days
    post_days = (inp.proposed_final_tx - inp.first_tx_after_gap).days  # F37-F36
    total_days = (inp.proposed_final_tx - inp.first_tx).days     # I42

    if rx_days <= 0:
        raise ValueError("Planned last treatment date must be after the first treatment date.")
    if pre_days < 0:
        raise ValueError("Last treatment before the gap cannot precede the first treatment.")
    if gap_days <= 0:
        raise ValueError("First treatment after the gap must be after the last treatment before it.")
    if post_days < 0:
        raise ValueError("Proposed final treatment cannot precede the first treatment after the gap.")

    pre_total = inp.pre_dose_per_fx_gy * inp.pre_fx
    post_total = inp.post_dose_per_fx_gy * inp.post_fx

    if inp.pre_fx >= rx_fx:
        warnings.append("Fractions delivered before the gap equal or exceed the prescription.")
    if pre_total + post_total > inp.rx_total_dose_gy + 10:
        warnings.append(
            "Proposed total dose exceeds the prescription by more than 10 Gy. "
            "Rule of thumb: never increase dose over about 10 Gy."
        )

    def tissue_block(name, t: TissueParams, is_tumor: bool) -> TissueBED:
        # Prescribed BED (F26 uses IF(T<Tk); F27/F28 subtract unconditionally)
        prescribed = bed(rx_fx, inp.rx_dose_per_fx_gy, t, rx_days, apply_tk=is_tumor)
        # Pre-gap BED evaluated at last tx before gap (C46: IF; C47/C48: unconditional)
        before = bed(inp.pre_fx, inp.pre_dose_per_fx_gy, t, pre_days, apply_tk=is_tumor)
        # Pre-gap BED evaluated at resumption (D46: IF; D47/D48: unconditional)
        before_resume = bed(inp.pre_fx, inp.pre_dose_per_fx_gy, t, resume_days, apply_tk=is_tumor)
        # Post-gap BED: K * (days of post phase), no Tk offset (F46-F48)
        after = (inp.post_fx * inp.post_dose_per_fx_gy
                 * (1.0 + inp.post_dose_per_fx_gy / t.alpha_beta)
                 - t.k * post_days)
        total = before_resume + after
        pct = (total / prescribed * 100.0) if prescribed else 0.0
        return TissueBED(
            name=name, alpha_beta=t.alpha_beta, k=t.k, tk=t.tk,
            prescribed=prescribed, before_gap=before,
            before_gap_at_resume=before_resume, after_gap=after,
            total=total, percent_of_rx=pct, delta=total - prescribed,
        )

    tumor = tissue_block("Tumor", inp.tumor, True)
    early = tissue_block("Early normal tissue", inp.early, False)
    late = tissue_block("Late normal tissue", inp.late, False)

    if late.percent_of_rx > 110:
        warnings.append(
            f"Late-responding normal tissue BED is {late.percent_of_rx:.1f}% of prescription."
        )

    fin = inp.proposed_final_tx
    summary = (
        f"Due to a treatment gap of {gap_days} days, the total dose delivered is determined "
        f"to be {pre_total + post_total:g} Gy. This is delivered in {inp.pre_fx} fx of "
        f"{inp.pre_dose_per_fx_gy:g} Gy before the gap and {inp.post_fx} fx of "
        f"{inp.post_dose_per_fx_gy:g} Gy after the gap. The treatment is to be completed on "
        f"{fin.month}/{fin.day}/{fin.year}."
    )

    return BEDResult(
        rx_fx=rx_fx, rx_calendar_days=rx_days,
        pre_total_dose=pre_total, pre_calendar_days=pre_days,
        gap_days=gap_days, post_total_dose=post_total, post_calendar_days=post_days,
        total_days=total_days, total_dose=pre_total + post_total,
        tumor=tumor, early=early, late=late, summary=summary, warnings=warnings,
    )


# --------------------------------------------------------------------------- #
#  Reference data (Alpha-Beta and Quick Rule of Thumb sheets)
# --------------------------------------------------------------------------- #

MODEL_PARAMETERS = [
    {"group": "Slow growing tumor (prostate)", "alpha_beta": "1.5", "k": "0.1", "tk": "—"},
    {"group": "Rapidly growing tumor", "alpha_beta": "10", "k": "0.6", "tk": "28"},
    {"group": "Early responding normal tissues", "alpha_beta": "10", "k": "0.2 – 0.3", "tk": "0"},
    {"group": "Late responding normal tissues", "alpha_beta": "2.5", "k": "0", "tk": "—"},
]

TISSUE_TOLERANCE = [
    ("Skin", "Desquamation", "50", "55 – 60", "10 – 12"),
    ("Skin", "Fibrosis", "5", "60 – 65", "2 – 3"),
    ("Skin", "Telangiectasia", "50", "55 – 60", "3 – 4"),
    ("Oral mucosa", "Confluent mucositis", "50", "65 – 70", "10"),
    ("Small intestine", "Late fibrosis, fistulae", "5", "50", "3 – 4"),
    ("Colon, rectum", "Late fibrosis, fistulae", "5", "60", "3 – 4"),
    ("Brain, spinal cord", "Necrosis", "5", "50", "2"),
    ("Brain, spinal cord", "Necrosis", "< 1", "60", "2"),
    ("Nerve plexus", "Demyelination, fibrosis", "5", "65", "3 – 4"),
    ("Lung", "Pneumonitis", "5", "20", "2 – 4"),
    ("Heart", "Pericarditis", "5", "35 – 40", "2 – 3"),
    ("Heart", "Pericarditis", "< 5", "50 – 60", "2 – 3"),
    ("Heart", "Cardiomyopathy", "50", "30 – 36", "2 – 3"),
    ("Heart", "Ischaemic heart disease", "15 – 20", "30", "2 – 3"),
    ("Kidney", "Glomerulosclerosis", "< 5", "20", "2 – 3"),
    ("Liver", "Hepatitis, VOD", "5", "25 – 30", "3"),
    ("Bladder", "Cystitis, ulcers", "< 5", "60 – 65", "5 – 10"),
]

RULE_OF_THUMB = {
    "columns": ["Not concerned about complications",
                "Concerned about late complications",
                "MD unwilling to specify"],
    "rows": [
        ("Tumor fast-growing", ["0.6 Gy per fx missed", "Max of 0.2 Gy per fx missed", "1 fx per 10 fx missed"]),
        ("Tumor slow-growing", ["0.1 Gy per fx missed", "—", "—"]),
    ],
    "notes": [
        "Never increase dose over about 10 Gy.",
        "Source: unpublished Colin Orton comments.",
        "For a formal evaluation use the BED calculator (LQ model) and equate late effects.",
    ],
}
