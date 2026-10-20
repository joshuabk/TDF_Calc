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

   On top of the spreadsheet's evaluator (`calculate_bed`), `calculate_bed_makeup`
   solves for the number of post-gap fractions that restores the prescribed tumor
   BED.  The post-gap finish date is not an input: it is derived from the fraction
   count and the treatment pattern (fractions per week) and re-evaluated as
   fractions are added, so the repopulation penalty is self-consistent.
"""

from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
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
    last_tx_before_break: date      # I32
    first_tx_after_break: date      # I33
    fx_per_day: int = 1             # E22
    fx_per_week: int = 5            # F22
    # Derived when None: treatment days from the start date through the last
    # treatment before the break, times fractions per day.
    fx_treated_before_break: Optional[int] = None   # D31
    # Both dates are derived when left as None: the planned end is the date of
    # the last prescribed fraction; the finish date is the date of the last
    # post-break fraction for the solved fraction count (self-consistent).
    original_end_date: Optional[date] = None    # I31
    proposed_finish_date: Optional[date] = None # I36


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
    original_end_date: Optional[date] = None
    proposed_finish_date: Optional[date] = None
    finish_date_derived: bool = False
    post_fx_whole: int = 0          # whole post-break fractions the finish date was built from
    fx_treated_before_break: int = 0
    fx_treated_from_calendar: bool = False
    warnings: list = field(default_factory=list)

    def as_dict(self):
        return asdict(self)


def _tdf_finish_date(inp: TDFInput, tdf2: float, fx_remaining: float):
    """
    Pick the post-break finish date self-consistently: the date of the N-th
    post-break fraction (treatment pattern fx_per_week / fx_per_day), where N
    is the whole number of fractions the TDF equation asks for over that very
    span.  Iterate from the originally remaining count; the TDF time term is
    weak so this settles in a few steps.  If it oscillates between two counts,
    keep the one whose continuous solution is nearest to it.
    """
    n = max(1, int(round(fx_remaining)))
    seen = {}
    for _ in range(25):
        end = nth_treatment_date(inp.first_tx_after_break, n, inp.fx_per_week, inp.fx_per_day)
        t2 = max(1, (end - inp.first_tx_after_break).days)
        n2 = tdf_fractions_for(tdf2, inp.dose_per_fx_cgy, t2)
        seen[n] = (abs(n2 - n), end)
        n_next = max(1, int(round(n2)))
        if n_next == n:
            return end, n
        if n_next in seen:
            best = min(seen, key=lambda k: seen[k][0])
            return seen[best][1], best
        n = n_next
    best = min(seen, key=lambda k: seen[k][0])
    return seen[best][1], best


def calculate_tdf(inp: TDFInput) -> TDFResult:
    warnings = []
    total_fx = inp.total_dose_cgy / inp.dose_per_fx_cgy
    if abs(total_fx - round(total_fx)) > 1e-9:
        warnings.append(
            f"Total dose / dose per fraction = {total_fx:.2f}, which is not a whole "
            "number of fractions. Check the prescription."
        )

    original_end = inp.original_end_date or nth_treatment_date(
        inp.start_date, int(round(total_fx)), inp.fx_per_week, inp.fx_per_day)
    original_days = (original_end - inp.start_date).days
    t1 = (inp.last_tx_before_break - inp.start_date).days
    rest = (inp.first_tx_after_break - inp.last_tx_before_break).days

    fx_from_calendar = inp.fx_treated_before_break is None
    if fx_from_calendar:
        fx_before = count_treatment_days(inp.start_date, inp.last_tx_before_break,
                                         inp.fx_per_week) * max(1, inp.fx_per_day)
        warnings.append(
            f"Fractions treated before the break ({fx_before}) were counted from the calendar "
            f"({inp.fx_per_week} fx/week, {inp.start_date:%m/%d/%Y} to {inp.last_tx_before_break:%m/%d/%Y}). "
            "If any treatment days were missed before the break, enter the delivered count directly."
        )
    else:
        fx_before = inp.fx_treated_before_break
    from dataclasses import replace as _replace
    inp = _replace(inp, fx_treated_before_break=fx_before)

    if original_days <= 0:
        raise ValueError("Original end date must be after the start date.")
    if t1 <= 0:
        raise ValueError("Date of last treatment before the break must be after the start date.")
    if rest <= 0:
        raise ValueError("First treatment after the break must be after the last treatment before it.")
    if inp.fx_treated_before_break <= 0 or inp.fx_treated_before_break >= total_fx:
        raise ValueError("Fractions treated before the break must be between 1 and the total fraction count.")

    tdf_total = tdf(total_fx, inp.dose_per_fx_cgy, original_days)
    tdf1 = tdf(inp.fx_treated_before_break, inp.dose_per_fx_cgy, t1)
    tdf_arp = tdf_decay(tdf1, t1, rest)
    tdf2 = tdf_total - tdf_arp

    fx_remaining = total_fx - inp.fx_treated_before_break
    derived = inp.proposed_finish_date is None
    if derived:
        finish, post_fx_whole = _tdf_finish_date(inp, tdf2, fx_remaining)
    else:
        finish, post_fx_whole = inp.proposed_finish_date, 0
    t2 = (finish - inp.first_tx_after_break).days
    if t2 <= 0 and not derived:
        raise ValueError("Proposed finish date must be after the first treatment after the break.")
    n2 = tdf_fractions_for(tdf2, inp.dose_per_fx_cgy, max(1, t2))
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
        original_end_date=original_end, proposed_finish_date=finish,
        finish_date_derived=derived, post_fx_whole=post_fx_whole,
        fx_treated_before_break=fx_before, fx_treated_from_calendar=fx_from_calendar,
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


# Defaults.  Tumor alpha/beta is 10 Gy (departmental practice; the spreadsheet's
# own default cell was 3, which is kept as the "worksheet default" preset).
# Early/late values are the spreadsheet's (BED sheet rows 17-19).
WORKSHEET_TUMOR = TissueParams(alpha_beta=3.0, k=0.6, tk=28)
DEFAULT_TUMOR = TissueParams(alpha_beta=10.0, k=0.6, tk=28)
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


def lq(n_fx: float, dose_per_fx_gy: float, tissue: TissueParams) -> float:
    """Linear-quadratic term N d (1 + d/(a/b)) without any time correction."""
    return n_fx * dose_per_fx_gy * (1.0 + dose_per_fx_gy / tissue.alpha_beta)


def repop_loss(tissue: TissueParams, overall_days: float) -> float:
    """
    BED lost to repopulation over a course of `overall_days` calendar days:
    K * (T - Tk), and nothing before the kick-off day Tk.  This is charged ONCE
    on the overall time from the first fraction to the last; splitting a course
    into phases and charging each phase separately is wrong whenever a phase
    starts before Tk (it would charge days that fall inside the lag).
    """
    if tissue.k == 0:
        return 0.0
    return tissue.k * max(0.0, overall_days - tissue.tk)


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
        # Prescribed BED over the planned course length.
        prescribed = lq(rx_fx, inp.rx_dose_per_fx_gy, t) - repop_loss(t, rx_days)
        lq_pre = lq(inp.pre_fx, inp.pre_dose_per_fx_gy, t)
        lq_post = lq(inp.post_fx, inp.post_dose_per_fx_gy, t)
        # Pre-gap BED evaluated at the last pre-gap treatment and at resumption
        # (the latter includes repopulation during the gap) -- informational.
        before = lq_pre - repop_loss(t, pre_days)
        before_resume = lq_pre - repop_loss(t, resume_days)
        # The delivered course as a whole: repopulation is charged once, on the
        # overall time first fraction -> last fraction.  (The worksheet's F46
        # cells charged K * post-gap days with no Tk offset, which is only right
        # when the patient resumes after day Tk; for an early, short gap it
        # over-charges by K * (Tk - resumption day).)
        total = lq_pre + lq_post - repop_loss(t, total_days)
        after = total - before_resume          # so the table's columns still add up
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
#  BED make-up solver: how many fractions must be added?
# --------------------------------------------------------------------------- #

MAX_POST_FX = 200
DEFAULT_FX_PER_WEEK = 5      # Monday-Friday; the page assumes this for every course


def is_treatment_day(d: date, fx_per_week: int) -> bool:
    """
    Treatment pattern used to turn a fraction count into a finish date.
    1-5 fx/week: the first `fx_per_week` weekdays of each week (5 = Mon-Fri).
    6: Mon-Sat.  7: every day.
    """
    if fx_per_week >= 7:
        return True
    return d.weekday() < max(1, fx_per_week)


def nth_treatment_date(start: date, n_fx: int, fx_per_week: int, fx_per_day: int = 1) -> date:
    """
    Date on which the n-th fraction is given when fraction 1 is on `start`.
    `start` is taken as a treatment day whatever its weekday (it is the date the
    patient actually resumed); subsequent fractions follow the weekly pattern.
    With `fx_per_day` = 2 (BID) each treatment day carries two fractions, so the
    n-th fraction falls on treatment day ceil(n / 2).  Returns `start` for n_fx <= 1.
    """
    if n_fx <= 1:
        return start
    fx_per_day = max(1, int(fx_per_day))
    days_needed = -(-n_fx // fx_per_day)   # ceil
    if days_needed <= 1:
        return start
    d = start
    day = 1
    while day < days_needed:
        d += timedelta(days=1)
        if is_treatment_day(d, fx_per_week):
            day += 1
    return d


def count_treatment_days(start: date, end: date, fx_per_week: int) -> int:
    """
    Treatment days from `start` through `end` inclusive.  `start` always counts
    (it is the date of the first fraction); later days follow the weekly pattern.
    """
    if end < start:
        return 0
    n = 1
    d = start
    while d < end:
        d += timedelta(days=1)
        if is_treatment_day(d, fx_per_week):
            n += 1
    return n


def resolve_pre_gap(inp):
    """
    Fill in the pre-gap fraction count and dose/fx when they were not entered.
    Returns (resolved_input, pre_fx_from_calendar: bool).
    """
    from dataclasses import replace
    from_calendar = inp.pre_fx is None
    pre_fx = inp.pre_fx if inp.pre_fx is not None else count_treatment_days(
        inp.first_tx, inp.last_tx_before_gap, inp.fx_per_week)
    pre_dose = inp.pre_dose_per_fx_gy if inp.pre_dose_per_fx_gy is not None else inp.rx_dose_per_fx_gy
    return replace(inp, pre_fx=pre_fx, pre_dose_per_fx_gy=pre_dose), from_calendar


def check_planned_course(inp) -> None:
    """
    The planned last treatment must be exactly the prescribed number of
    treatment days (Mon-Fri) after the first treatment: the planned span sets
    the repopulation budget of the prescription, so a wrong date silently
    changes the make-up.
    """
    rx_fx = inp.rx_total_dose_gy / inp.rx_dose_per_fx_gy
    if abs(rx_fx - round(rx_fx)) > 1e-9:
        return
    expected = nth_treatment_date(inp.first_tx, int(round(rx_fx)), inp.fx_per_week)
    if inp.planned_last_tx != expected:
        have = count_treatment_days(inp.first_tx, inp.planned_last_tx, inp.fx_per_week)
        raise ValueError(
            f"The planned last treatment date gives {have} treatment days for a {rx_fx:g}-fraction "
            f"prescription. {rx_fx:g} fractions Mon–Fri from {inp.first_tx:%m/%d/%Y} end on "
            f"{expected:%m/%d/%Y}."
        )


def _calendar_consistency_warnings(inp, pre_from_calendar: bool) -> list:
    """
    When the pre-gap fraction count came from the calendar, remind the user to
    enter it directly if any treatment days were missed before the gap.
    """
    if not pre_from_calendar:
        return []
    return [
        f"Fractions delivered before the gap ({inp.pre_fx}) were counted from the calendar, "
        f"Mon–Fri {inp.first_tx:%m/%d/%Y} to {inp.last_tx_before_gap:%m/%d/%Y}. If any treatment "
        "days were missed before the gap (holidays, machine down), enter the delivered count directly."
    ]


@dataclass
class BEDMakeupInput:
    # Original prescription
    rx_dose_per_fx_gy: float
    rx_total_dose_gy: float
    # Dates (no proposed finish date: it is solved for)
    first_tx: date
    planned_last_tx: date
    last_tx_before_gap: date
    first_tx_after_gap: date
    # Post-gap: dose per fraction is chosen, fraction count is solved
    post_dose_per_fx_gy: float
    fx_per_week: int = 5
    fx_per_day: int = 1             # 1 = once daily, 2 = BID (assumes >= 6 h between fractions)
    # Pre-gap delivered.  Both are optional: left as None, the fraction count is
    # the number of treatment days from first_tx through last_tx_before_gap at
    # fx_per_week (once daily), and the dose/fx is the prescription dose/fx.
    pre_fx: Optional[int] = None
    pre_dose_per_fx_gy: Optional[float] = None
    # Tissue parameters
    tumor: TissueParams = field(default_factory=lambda: DEFAULT_TUMOR)
    early: TissueParams = field(default_factory=lambda: DEFAULT_EARLY)
    late: TissueParams = field(default_factory=lambda: DEFAULT_LATE)


@dataclass
class BEDMakeupResult:
    pre_fx: int                     # fractions delivered before the gap (entered or from calendar)
    pre_dose_per_fx: float
    pre_fx_from_calendar: bool
    post_fx_required: int           # whole N whose tumor BED is closest to the prescription
    post_fx_exact: float            # continuous N at that finish date (for display)
    tumor_pct_required: float       # tumor BED % of Rx at post_fx_required
    fx_remaining_planned: float     # rx_fx - pre_fx
    fractions_to_add: int           # post_fx_required - fx_remaining_planned
    final_tx_date: date             # date of the last post-gap fraction
    post_calendar_days: int         # first_tx_after_gap -> final_tx_date
    post_total_dose: float          # post_fx_required * d
    total_dose: float               # pre + post physical dose
    extra_dose: float               # total_dose - rx_total_dose
    bed: BEDResult                  # full evaluation of the solved schedule
    warnings: list = field(default_factory=list)
    neighbours: list = field(default_factory=list)   # [(n, tumor %, finish date)] for N-1, N, N+1

    def as_dict(self):
        d = asdict(self)
        d["bed"] = self.bed.as_dict()
        return d


def _tumor_total_for(inp: BEDMakeupInput, n_post: int, rx_fx: float):
    """Tumor BED total (before-at-resume + after) for `n_post` post-gap fractions."""
    end = nth_treatment_date(inp.first_tx_after_gap, n_post, inp.fx_per_week, inp.fx_per_day)
    post_days = (end - inp.first_tx_after_gap).days if n_post > 0 else 0
    if n_post > 0:
        total_days = (end - inp.first_tx).days
    else:
        total_days = (inp.last_tx_before_gap - inp.first_tx).days
    lq_pre = lq(inp.pre_fx, inp.pre_dose_per_fx_gy, inp.tumor)
    total = lq_pre + lq(n_post, inp.post_dose_per_fx_gy, inp.tumor) - repop_loss(inp.tumor, total_days)
    return total, end, post_days, total_days


def calculate_bed_makeup(inp: BEDMakeupInput) -> BEDMakeupResult:
    """
    Find the whole number of post-gap fractions N (at the chosen post-gap
    dose/fx) whose tumor BED_total is closest to tumor BED_prescribed, where the
    finish date — and hence the repopulation penalty K * (post-gap days) — is
    derived from N itself.  Each extra fraction adds d(1 + d/(a/b)) of BED but
    also extends the course, which costs K per added calendar day.  The search
    steps N upward until the prescription is met, then compares that N with
    N - 1 and keeps whichever lands nearer 100 %: a whole fraction cannot hit
    the prescription exactly, and 99.9 % is a better answer than 103 %.
    """
    if inp.post_dose_per_fx_gy <= 0:
        raise ValueError("Post-gap dose per fraction must be greater than zero.")
    if inp.fx_per_week < 1:
        raise ValueError("Fractions per week must be at least 1.")
    if inp.fx_per_day < 1:
        raise ValueError("Fractions per day must be at least 1.")
    check_planned_course(inp)
    inp, pre_from_calendar = resolve_pre_gap(inp)
    if inp.pre_fx < 1:
        raise ValueError("Fractions delivered before the gap must be at least 1.")
    calendar_warnings = _calendar_consistency_warnings(inp, pre_from_calendar)

    rx_fx = inp.rx_total_dose_gy / inp.rx_dose_per_fx_gy
    rx_days = (inp.planned_last_tx - inp.first_tx).days
    if rx_days <= 0:
        raise ValueError("Planned last treatment date must be after the first treatment date.")
    if (inp.first_tx_after_gap - inp.last_tx_before_gap).days <= 0:
        raise ValueError("First treatment after the gap must be after the last treatment before it.")

    if inp.pre_fx >= rx_fx:
        raise ValueError(
            f"Fractions delivered before the gap ({inp.pre_fx}) equal or exceed the prescribed "
            f"fraction count ({rx_fx:g}). Check the dates, or enter the delivered count directly."
        )

    prescribed = lq(rx_fx, inp.rx_dose_per_fx_gy, inp.tumor) - repop_loss(inp.tumor, rx_days)

    warnings = list(calendar_warnings)
    n = 0
    total, end, post_days, total_days = _tumor_total_for(inp, n, rx_fx)
    while total < prescribed - 1e-9:
        n += 1
        if n > MAX_POST_FX:
            raise ValueError(
                f"No schedule of up to {MAX_POST_FX} post-gap fractions restores the tumor BED: "
                "repopulation outpaces the dose per fraction. Increase the post-gap dose per "
                "fraction or fractions per week."
            )
        total, end, post_days, total_days = _tumor_total_for(inp, n, rx_fx)

    # n is the first count that meets the prescription; n - 1 falls just short.
    # Keep whichever is nearer the prescription.
    if n > 0:
        total_below, _, _, _ = _tumor_total_for(inp, n - 1, rx_fx)
        if abs(prescribed - total_below) < abs(total - prescribed):
            n -= 1
            total, end, post_days, total_days = _tumor_total_for(inp, n, rx_fx)

    neighbours = []
    for m in (n - 1, n, n + 1):
        if m < 0:
            continue
        t_m, end_m, _, _ = _tumor_total_for(inp, m, rx_fx)
        neighbours.append((m, (t_m / prescribed * 100.0) if prescribed else 0.0, end_m))

    # Continuous solution at the solved finish date (what the spreadsheet user
    # would get by hand with that end date), for display next to the whole number.
    d = inp.post_dose_per_fx_gy
    per_fx = d * (1.0 + d / inp.tumor.alpha_beta)
    exact = (prescribed - lq(inp.pre_fx, inp.pre_dose_per_fx_gy, inp.tumor)
             + repop_loss(inp.tumor, total_days)) / per_fx
    exact = max(0.0, exact)

    if n == 0:
        warnings.append(
            "The dose already delivered before the gap meets or exceeds the prescribed tumor "
            "BED; no post-gap fractions are required."
        )

    fx_remaining = rx_fx - inp.pre_fx
    to_add = int(round(n - fx_remaining))

    evaluated = calculate_bed(BEDInput(
        rx_dose_per_fx_gy=inp.rx_dose_per_fx_gy, rx_total_dose_gy=inp.rx_total_dose_gy,
        first_tx=inp.first_tx, planned_last_tx=inp.planned_last_tx,
        last_tx_before_gap=inp.last_tx_before_gap, first_tx_after_gap=inp.first_tx_after_gap,
        proposed_final_tx=end,
        pre_dose_per_fx_gy=inp.pre_dose_per_fx_gy, pre_fx=inp.pre_fx,
        post_dose_per_fx_gy=d, post_fx=n,
        tumor=inp.tumor, early=inp.early, late=inp.late,
    ))
    warnings.extend(evaluated.warnings)

    post_total = n * d
    total_dose = inp.pre_dose_per_fx_gy * inp.pre_fx + post_total
    return BEDMakeupResult(
        pre_fx=inp.pre_fx, pre_dose_per_fx=inp.pre_dose_per_fx_gy, pre_fx_from_calendar=pre_from_calendar,
        post_fx_required=n, post_fx_exact=exact,
        tumor_pct_required=(total / prescribed * 100.0) if prescribed else 0.0,
        neighbours=neighbours,
        fx_remaining_planned=fx_remaining, fractions_to_add=to_add,
        final_tx_date=end, post_calendar_days=post_days,
        post_total_dose=post_total, total_dose=total_dose,
        extra_dose=total_dose - inp.rx_total_dose_gy,
        bed=evaluated, warnings=warnings,
    )


# --------------------------------------------------------------------------- #
#  BED make-up solver, alternative: raise the dose per fraction instead
# --------------------------------------------------------------------------- #

DOSE_STEP_GY = 0.01   # solved dose/fx is rounded UP to the nearest cGy


@dataclass
class BEDDoseMakeupResult:
    pre_fx: int
    pre_dose_per_fx: float
    pre_fx_from_calendar: bool
    post_fx: int                    # fixed: the fractions still owed (rx_fx - pre_fx)
    dose_per_fx_exact: float        # continuous solution (Gy)
    dose_per_fx: float              # rounded up to DOSE_STEP_GY, used for the evaluation
    dose_per_fx_original: float     # the prescription's dose/fx, for comparison
    dose_increase_per_fx: float     # dose_per_fx - dose_per_fx_original
    final_tx_date: date
    post_calendar_days: int
    post_total_dose: float
    total_dose: float
    extra_dose: float
    bed: BEDResult
    warnings: list = field(default_factory=list)

    def as_dict(self):
        d = asdict(self)
        d["bed"] = self.bed.as_dict()
        return d


def solve_dose_per_fx(n_fx: float, target_lq_bed: float, alpha_beta: float) -> float:
    """
    Invert N d (1 + d/(a/b)) = target for d (positive root of the quadratic).
    """
    if n_fx <= 0:
        raise ValueError("Fraction count must be greater than zero.")
    if target_lq_bed <= 0:
        return 0.0
    return 0.5 * alpha_beta * (-1.0 + (1.0 + 4.0 * target_lq_bed / (n_fx * alpha_beta)) ** 0.5)


def calculate_bed_dose_makeup(inp: BEDMakeupInput) -> BEDDoseMakeupResult:
    """
    Keep the number of post-gap fractions equal to the fractions still owed on
    the prescription (no extra treatment days) and solve for the post-gap dose
    per fraction d that restores the prescribed tumor BED:

        N d (1 + d/(a/b)) = BED_rx - BED_before(at resumption) + K * T_post

    T_post is the calendar span of those N fractions at the chosen fx/week, so
    it is fixed and the equation is a quadratic in d.  `inp.post_dose_per_fx_gy`
    is ignored (it is the unknown).
    """
    if inp.fx_per_week < 1:
        raise ValueError("Fractions per week must be at least 1.")
    if inp.fx_per_day < 1:
        raise ValueError("Fractions per day must be at least 1.")
    check_planned_course(inp)
    inp, pre_from_calendar = resolve_pre_gap(inp)
    if inp.pre_fx < 1:
        raise ValueError("Fractions delivered before the gap must be at least 1.")
    calendar_warnings = _calendar_consistency_warnings(inp, pre_from_calendar)

    rx_fx = inp.rx_total_dose_gy / inp.rx_dose_per_fx_gy
    n = int(round(rx_fx - inp.pre_fx))
    if n < 1:
        raise ValueError(
            f"Fractions delivered before the gap ({inp.pre_fx}) equal or exceed the prescribed "
            f"fraction count ({rx_fx:g}); nothing remains to re-dose. Check the dates, or enter "
            "the delivered count directly."
        )
    rx_days = (inp.planned_last_tx - inp.first_tx).days
    if rx_days <= 0:
        raise ValueError("Planned last treatment date must be after the first treatment date.")
    if (inp.first_tx_after_gap - inp.last_tx_before_gap).days <= 0:
        raise ValueError("First treatment after the gap must be after the last treatment before it.")

    prescribed = lq(rx_fx, inp.rx_dose_per_fx_gy, inp.tumor) - repop_loss(inp.tumor, rx_days)
    end = nth_treatment_date(inp.first_tx_after_gap, n, inp.fx_per_week, inp.fx_per_day)
    post_days = (end - inp.first_tx_after_gap).days
    total_days = (end - inp.first_tx).days

    # LQ dose the post-gap fractions must deliver: the prescription, less what
    # the pre-gap fractions are worth, plus the repopulation charged on the whole
    # (now longer) course.
    target = (prescribed - lq(inp.pre_fx, inp.pre_dose_per_fx_gy, inp.tumor)
              + repop_loss(inp.tumor, total_days))
    exact = solve_dose_per_fx(n, target, inp.tumor.alpha_beta)
    # Round up to the cGy so the delivered BED is never below prescription.
    steps = int(exact / DOSE_STEP_GY - 1e-9) + 1 if exact > 0 else 0
    d = round(steps * DOSE_STEP_GY, 2)

    warnings = list(calendar_warnings)
    if exact <= 0:
        warnings.append(
            "The dose already delivered before the gap meets or exceeds the prescribed tumor "
            "BED; no further dose is required."
        )

    evaluated = calculate_bed(BEDInput(
        rx_dose_per_fx_gy=inp.rx_dose_per_fx_gy, rx_total_dose_gy=inp.rx_total_dose_gy,
        first_tx=inp.first_tx, planned_last_tx=inp.planned_last_tx,
        last_tx_before_gap=inp.last_tx_before_gap, first_tx_after_gap=inp.first_tx_after_gap,
        proposed_final_tx=end,
        pre_dose_per_fx_gy=inp.pre_dose_per_fx_gy, pre_fx=inp.pre_fx,
        post_dose_per_fx_gy=d, post_fx=n,
        tumor=inp.tumor, early=inp.early, late=inp.late,
    ))
    warnings.extend(evaluated.warnings)

    post_total = n * d
    total_dose = inp.pre_dose_per_fx_gy * inp.pre_fx + post_total
    return BEDDoseMakeupResult(
        pre_fx=inp.pre_fx, pre_dose_per_fx=inp.pre_dose_per_fx_gy, pre_fx_from_calendar=pre_from_calendar,
        post_fx=n, dose_per_fx_exact=exact, dose_per_fx=d,
        dose_per_fx_original=inp.rx_dose_per_fx_gy,
        dose_increase_per_fx=d - inp.rx_dose_per_fx_gy,
        final_tx_date=end, post_calendar_days=post_days,
        post_total_dose=post_total, total_dose=total_dose,
        extra_dose=total_dose - inp.rx_total_dose_gy,
        bed=evaluated, warnings=warnings,
    )


# --------------------------------------------------------------------------- #
#  Scenario comparison: both strategies, once daily and BID
# --------------------------------------------------------------------------- #

@dataclass
class Scenario:
    key: str                # e.g. "fractions-1", "dose-2"
    mode: str               # "fractions" | "dose"
    fx_per_day: int
    label: str
    schedule: str           # human-readable post-gap schedule
    post_fx: int
    dose_per_fx: float
    fractions_to_add: int
    post_calendar_days: int
    final_tx_date: date
    total_dose: float
    extra_dose: float
    tumor_pct: float
    early_pct: float
    late_pct: float
    error: Optional[str] = None


def _failed_scenario(key, mode, fx_per_day, label, when, exc):
    return Scenario(key, mode, fx_per_day, label, "—", 0, 0.0, 0, 0, when,
                    0.0, 0.0, 0.0, 0.0, 0.0, error=str(exc))


def compare_schedules(inp: BEDMakeupInput) -> list:
    """
    Run the make-up options on the same inputs so they can be set side by side:
    add fractions (once daily), raise dose/fx (once daily), and BID (raise
    dose/fx over the remaining fractions at 2 fx/day).  Adding fractions BID is
    not offered: BID is used precisely to keep the fraction count.  A scenario
    that cannot be solved is returned with `error` set instead of raising.
    """
    from dataclasses import replace
    out = []

    key, label = "fractions-1", "Add fractions"
    try:
        r = calculate_bed_makeup(replace(inp, fx_per_day=1))
        out.append(Scenario(
            key=key, mode="fractions", fx_per_day=1, label=label,
            schedule=f"{r.post_fx_required} fx × {inp.post_dose_per_fx_gy:g} Gy",
            post_fx=r.post_fx_required, dose_per_fx=inp.post_dose_per_fx_gy,
            fractions_to_add=r.fractions_to_add, post_calendar_days=r.post_calendar_days,
            final_tx_date=r.final_tx_date, total_dose=r.total_dose, extra_dose=r.extra_dose,
            tumor_pct=r.bed.tumor.percent_of_rx, early_pct=r.bed.early.percent_of_rx,
            late_pct=r.bed.late.percent_of_rx,
        ))
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        out.append(_failed_scenario(key, "fractions", 1, label, inp.first_tx_after_gap, exc))

    for fx_per_day, label in ((1, "Raise dose/fx"), (2, "BID")):
        key = f"dose-{fx_per_day}"
        try:
            r = calculate_bed_dose_makeup(replace(inp, fx_per_day=fx_per_day))
            out.append(Scenario(
                key=key, mode="dose", fx_per_day=fx_per_day, label=label,
                schedule=f"{r.post_fx} fx × {r.dose_per_fx:.2f} Gy"
                         + (" BID" if fx_per_day == 2 else ""),
                post_fx=r.post_fx, dose_per_fx=r.dose_per_fx,
                fractions_to_add=0, post_calendar_days=r.post_calendar_days,
                final_tx_date=r.final_tx_date, total_dose=r.total_dose, extra_dose=r.extra_dose,
                tumor_pct=r.bed.tumor.percent_of_rx, early_pct=r.bed.early.percent_of_rx,
                late_pct=r.bed.late.percent_of_rx,
            ))
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            out.append(_failed_scenario(key, "dose", fx_per_day, label, inp.first_tx_after_gap, exc))
    return out


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
