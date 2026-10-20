from datetime import date

from django.test import TestCase
from django.urls import reverse

from .engine import (
    DEFAULT_TUMOR, WORKSHEET_TUMOR, BEDInput, BEDMakeupInput, TDFInput, TissueParams, calculate_bed, calculate_bed_dose_makeup,
    calculate_bed_makeup, calculate_tdf, compare_schedules, count_treatment_days,
    nth_treatment_date, solve_dose_per_fx, tdf, tdf_fractions_for,
)


class BEDEngineTests(TestCase):
    """Values hand-computed from the departmental spreadsheet example (BED sheet)."""

    def setUp(self):
        self.inp = BEDInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2018, 12, 12), planned_last_tx=date(2019, 1, 18),
            last_tx_before_gap=date(2019, 1, 11), first_tx_after_gap=date(2019, 2, 11),
            proposed_final_tx=date(2019, 2, 15),
            pre_dose_per_fx_gy=2, pre_fx=20, post_dose_per_fx_gy=2, post_fx=5,
            tumor=WORKSHEET_TUMOR,
        )
        self.res = calculate_bed(self.inp)

    def test_dates(self):
        self.assertEqual(self.res.rx_calendar_days, 37)
        self.assertEqual(self.res.pre_calendar_days, 30)
        self.assertEqual(self.res.gap_days, 31)
        self.assertEqual(self.res.post_calendar_days, 4)
        self.assertEqual(self.res.total_days, 65)

    def test_tumor(self):
        t = self.res.tumor
        self.assertAlmostEqual(t.prescribed, 77.9333, places=3)        # F26
        self.assertAlmostEqual(t.before_gap, 65.4667, places=3)        # C46
        self.assertAlmostEqual(t.before_gap_at_resume, 46.8667, places=3)  # D46
        self.assertAlmostEqual(t.after_gap, 14.2667, places=3)         # F46
        self.assertAlmostEqual(t.total, 61.1333, places=3)             # H46
        self.assertAlmostEqual(t.percent_of_rx, 78.44, places=1)       # I46

    def test_early(self):
        e = self.res.early
        self.assertAlmostEqual(e.prescribed, 50.75, places=3)
        self.assertAlmostEqual(e.before_gap, 40.5, places=3)
        self.assertAlmostEqual(e.before_gap_at_resume, 32.75, places=3)
        self.assertAlmostEqual(e.after_gap, 11.0, places=3)
        self.assertAlmostEqual(e.total, 43.75, places=3)

    def test_late(self):
        l = self.res.late
        self.assertAlmostEqual(l.prescribed, 90.0, places=3)
        self.assertAlmostEqual(l.total, 90.0, places=3)
        self.assertAlmostEqual(l.percent_of_rx, 100.0, places=3)

    def test_summary_matches_spreadsheet_text(self):
        self.assertEqual(
            self.res.summary,
            "Due to a treatment gap of 31 days, the total dose delivered is determined to be "
            "50 Gy. This is delivered in 20 fx of 2 Gy before the gap and 5 fx of 2 Gy after "
            "the gap. The treatment is to be completed on 2/15/2019.",
        )

    def test_tumor_below_tk_has_no_repopulation(self):
        inp = self.inp
        inp.planned_last_tx = date(2019, 1, 5)  # 24 days < Tk 28
        r = calculate_bed(inp)
        self.assertAlmostEqual(r.tumor.prescribed, 25 * 2 * (1 + 2 / 3), places=4)


class BEDMakeupSolverTests(TestCase):
    """Solve for post-gap fractions on the spreadsheet example (resumption Mon 2019-02-11)."""

    def setUp(self):
        self.inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2018, 12, 12), planned_last_tx=date(2019, 1, 15),
            last_tx_before_gap=date(2019, 1, 11), first_tx_after_gap=date(2019, 2, 11),
            pre_dose_per_fx_gy=2, pre_fx=20, post_dose_per_fx_gy=2, fx_per_week=5,
            tumor=WORKSHEET_TUMOR,
        )

    def test_treatment_calendar(self):
        mon = date(2019, 2, 11)
        self.assertEqual(nth_treatment_date(mon, 1, 5), mon)
        self.assertEqual(nth_treatment_date(mon, 5, 5), date(2019, 2, 15))   # Fri
        self.assertEqual(nth_treatment_date(mon, 6, 5), date(2019, 2, 18))   # skips weekend
        self.assertEqual(nth_treatment_date(mon, 13, 5), date(2019, 2, 27))
        self.assertEqual(nth_treatment_date(mon, 8, 7), date(2019, 2, 18))   # daily
        # Resumption on a Saturday still counts as fraction 1
        self.assertEqual(nth_treatment_date(date(2019, 2, 9), 2, 5), date(2019, 2, 11))

    def test_hand_computed_solution(self):
        # Planned course 12/12 - 1/15 = 34 d -> prescribed 83.33 - 0.6*6 = 79.73.
        # 12 fx end Tue 2/26 (total 76 d): 106.67 - 0.6*48 = 77.87 = 97.7 %
        # 13 fx end Wed 2/27 (total 77 d): 110.00 - 0.6*49 = 80.60 = 101.1 %  <- nearer
        r = calculate_bed_makeup(self.inp)
        self.assertEqual(r.bed.rx_calendar_days, 34)
        self.assertAlmostEqual(r.bed.tumor.prescribed, 79.7333, places=3)
        self.assertEqual(r.post_fx_required, 13)
        self.assertEqual(r.fractions_to_add, 8)           # 13 - (25 - 20)
        self.assertEqual(r.final_tx_date, date(2019, 2, 27))
        self.assertEqual(r.post_calendar_days, 16)
        self.assertAlmostEqual(r.post_fx_exact, 12.74, places=2)
        self.assertEqual(r.total_dose, 66)
        self.assertEqual(r.extra_dose, 16)
        self.assertAlmostEqual(r.bed.tumor.total, 80.6, places=4)
        self.assertAlmostEqual(r.tumor_pct_required, 101.09, places=1)
        self.assertEqual([n[0] for n in r.neighbours], [12, 13, 14])
        self.assertAlmostEqual(r.neighbours[0][1], 97.66, places=1)

    def test_rounds_up_when_the_higher_count_is_nearer(self):
        # Shift resumption to Tue 2/12: the same 12 fx then end Wed 2/27 (15 d) -> same 99.9 %,
        # so instead vary K so that the lower count undershoots badly.
        inp = self.inp
        inp.tumor = TissueParams(alpha_beta=3, k=0.5, tk=28)
        r = calculate_bed_makeup(inp)
        lo = [n for n in r.neighbours if n[0] == r.post_fx_required - 1][0]
        hi = [n for n in r.neighbours if n[0] == r.post_fx_required + 1][0]
        chosen = [n for n in r.neighbours if n[0] == r.post_fx_required][0]
        self.assertLessEqual(abs(chosen[1] - 100), abs(lo[1] - 100))
        self.assertLessEqual(abs(chosen[1] - 100), abs(hi[1] - 100))

    def test_one_fewer_fraction_is_short(self):
        inp = self.inp
        short = calculate_bed(BEDInput(
            tumor=WORKSHEET_TUMOR,
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=inp.first_tx, planned_last_tx=inp.planned_last_tx,
            last_tx_before_gap=inp.last_tx_before_gap, first_tx_after_gap=inp.first_tx_after_gap,
            proposed_final_tx=date(2019, 2, 26),
            pre_dose_per_fx_gy=2, pre_fx=20, post_dose_per_fx_gy=2, post_fx=12,
        ))
        self.assertLess(short.tumor.total, short.tumor.prescribed)

    def test_solved_schedule_is_evaluated_consistently(self):
        r = calculate_bed_makeup(self.inp)
        self.assertEqual(r.bed.post_calendar_days, r.post_calendar_days)
        self.assertEqual(r.bed.post_total_dose, r.post_total_dose)
        self.assertIn("more than 10 Gy", " ".join(r.warnings))

    def test_no_repopulation_needs_only_planned_fractions(self):
        inp = self.inp
        inp.tumor = TissueParams(alpha_beta=3, k=0.0, tk=0)
        r = calculate_bed_makeup(inp)
        self.assertEqual(r.post_fx_required, 5)
        self.assertEqual(r.fractions_to_add, 0)

    def test_higher_dose_per_fraction_needs_fewer_fractions(self):
        inp = self.inp
        inp.post_dose_per_fx_gy = 3
        r = calculate_bed_makeup(inp)
        self.assertLess(r.post_fx_required, 13)
        self.assertEqual(r.post_fx_required, 6)

    def test_repopulation_outpacing_dose_raises(self):
        inp = self.inp
        inp.tumor = TissueParams(alpha_beta=3, k=5.0, tk=28)
        inp.pre_fx = 5          # most of the course still owed; every added fx costs more than it adds
        with self.assertRaises(ValueError):
            calculate_bed_makeup(inp)


class BEDDoseMakeupSolverTests(TestCase):
    """Raise the dose per fraction over the 5 remaining fractions (Mon 2/11 - Fri 2/15, 4 days)."""

    def setUp(self):
        self.inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2018, 12, 12), planned_last_tx=date(2019, 1, 15),
            last_tx_before_gap=date(2019, 1, 11), first_tx_after_gap=date(2019, 2, 11),
            pre_dose_per_fx_gy=2, pre_fx=20, post_dose_per_fx_gy=2, fx_per_week=5,
            tumor=WORKSHEET_TUMOR,
        )

    def test_quadratic_inverse(self):
        n, d, ab = 5, 3.0, 3.0
        self.assertAlmostEqual(solve_dose_per_fx(n, n * d * (1 + d / ab), ab), d, places=9)
        self.assertEqual(solve_dose_per_fx(5, 0, 3), 0.0)

    def test_hand_computed_solution(self):
        # Course 12/12 - 2/15 = 65 d: target = 79.7333 - 66.6667 + 0.6*(65-28) = 35.2667 = 5 d (1 + d/3)
        r = calculate_bed_dose_makeup(self.inp)
        self.assertEqual(r.post_fx, 5)
        self.assertEqual(r.final_tx_date, date(2019, 2, 15))
        self.assertEqual(r.post_calendar_days, 4)
        d = r.dose_per_fx_exact
        self.assertAlmostEqual(5 * d * (1 + d / 3), 35.2667, places=3)
        self.assertAlmostEqual(d, 3.33839, places=4)
        self.assertEqual(r.dose_per_fx, 3.34)                 # rounded up to the cGy
        self.assertAlmostEqual(r.total_dose, 56.7, places=6)
        self.assertAlmostEqual(r.extra_dose, 6.7, places=6)
        self.assertGreaterEqual(r.bed.tumor.total, r.bed.tumor.prescribed)
        self.assertLess(r.bed.tumor.percent_of_rx, 100.5)

    def test_no_repopulation_keeps_original_dose(self):
        inp = self.inp
        inp.tumor = TissueParams(alpha_beta=3, k=0.0, tk=0)
        r = calculate_bed_dose_makeup(inp)
        self.assertEqual(r.dose_per_fx, 2.0)
        self.assertEqual(r.extra_dose, 0)

    def test_rounding_never_undershoots(self):
        for k in (0.3, 0.45, 0.6, 0.9):
            inp = self.inp
            inp.tumor = TissueParams(alpha_beta=3, k=k, tk=28)
            r = calculate_bed_dose_makeup(inp)
            self.assertGreaterEqual(r.bed.tumor.total + 1e-9, r.bed.tumor.prescribed, k)
            self.assertLessEqual(r.dose_per_fx - r.dose_per_fx_exact, 0.01 + 1e-9)


class BIDTests(TestCase):
    """Two fractions per treatment day: same BED per fraction, shorter calendar."""

    def setUp(self):
        self.inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2018, 12, 12), planned_last_tx=date(2019, 1, 15),
            last_tx_before_gap=date(2019, 1, 11), first_tx_after_gap=date(2019, 2, 11),
            pre_dose_per_fx_gy=2, pre_fx=20, post_dose_per_fx_gy=2, fx_per_week=5, fx_per_day=2,
            tumor=WORKSHEET_TUMOR,
        )

    def test_bid_calendar(self):
        mon = date(2019, 2, 11)
        self.assertEqual(nth_treatment_date(mon, 1, 5, 2), mon)
        self.assertEqual(nth_treatment_date(mon, 2, 5, 2), mon)                 # both on day 1
        self.assertEqual(nth_treatment_date(mon, 3, 5, 2), date(2019, 2, 12))
        self.assertEqual(nth_treatment_date(mon, 10, 5, 2), date(2019, 2, 15))  # 5 days
        self.assertEqual(nth_treatment_date(mon, 11, 5, 2), date(2019, 2, 18))  # 6th day, skips weekend

    def test_add_fractions_bid(self):
        # 10 fx end Fri 2/15 (65 d total): 100 - 0.6*37 = 77.8 (97.6 %)
        # 11 fx end Mon 2/18 (68 d total): 103.33 - 0.6*40 = 79.33 (99.5 %)  -> nearer
        r = calculate_bed_makeup(self.inp)
        self.assertEqual(r.post_fx_required, 11)
        self.assertEqual(r.fractions_to_add, 6)
        self.assertEqual(r.final_tx_date, date(2019, 2, 18))
        self.assertEqual(r.post_calendar_days, 7)
        self.assertAlmostEqual(r.tumor_pct_required, 99.50, places=1)

    def test_raise_dose_bid(self):
        # 5 fx BID -> 3 treatment days Mon-Wed; course 12/12 - 2/13 = 63 d:
        # target = 79.7333 - 66.6667 + 0.6*35 = 34.0667
        r = calculate_bed_dose_makeup(self.inp)
        self.assertEqual(r.post_fx, 5)
        self.assertEqual(r.final_tx_date, date(2019, 2, 13))
        self.assertEqual(r.post_calendar_days, 2)
        d = r.dose_per_fx_exact
        self.assertAlmostEqual(5 * d * (1 + d / 3), 34.0667, places=3)
        self.assertEqual(r.dose_per_fx, 3.27)

    def test_bid_needs_less_than_once_daily(self):
        from dataclasses import replace
        qd = replace(self.inp, fx_per_day=1)
        self.assertLess(calculate_bed_makeup(self.inp).extra_dose, calculate_bed_makeup(qd).extra_dose)
        self.assertLess(calculate_bed_dose_makeup(self.inp).extra_dose,
                        calculate_bed_dose_makeup(qd).extra_dose)

    def test_compare_schedules(self):
        sc = compare_schedules(self.inp)
        self.assertEqual([x.key for x in sc], ["fractions-1", "dose-1", "dose-2"])
        self.assertTrue(all(x.error is None for x in sc))
        by = {x.key: x for x in sc}
        self.assertEqual(by["fractions-1"].fractions_to_add, 8)
        self.assertEqual(by["dose-1"].dose_per_fx, 3.34)
        self.assertEqual(by["dose-2"].dose_per_fx, 3.27)
        self.assertEqual(by["dose-2"].schedule, "5 fx × 3.27 Gy BID")
        for x in sc:
            self.assertGreaterEqual(x.tumor_pct, 99.0)      # dose modes >= 100; add-fractions nearest

    def test_compare_schedules_reports_unsolvable(self):
        inp = self.inp
        inp.tumor = TissueParams(alpha_beta=3, k=5.0, tk=28)
        inp.pre_fx = 5
        sc = compare_schedules(inp)
        self.assertIsNotNone(sc[0].error)       # add-fractions cannot catch up
        self.assertIsNone(sc[1].error)          # raising dose/fx always has a root


class EarlyShortGapTests(TestCase):
    """
    Regression for the over-charging bug Josh found: 25 x 2 Gy from Mon 10/5/2026,
    one missed day (last tx 10/12, resumed 10/14).  Repopulation must be charged
    once on the overall course, not per phase; charging the post-gap phase from
    day 0 added ~11 Gy of spurious loss.
    """

    def setUp(self):
        self.inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2026, 10, 5), planned_last_tx=date(2026, 11, 6),   # 25 fx Mon-Fri
            last_tx_before_gap=date(2026, 10, 12), first_tx_after_gap=date(2026, 10, 14),
            post_dose_per_fx_gy=2,
        )   # default tumor: a/b 10, K 0.6, Tk 28

    def test_one_missed_day_costs_about_one_day_of_repopulation(self):
        r = calculate_bed_dose_makeup(self.inp)
        self.assertEqual(r.pre_fx, 6)
        self.assertEqual(r.post_fx, 19)
        # planned 10/5-11/6 = 32 d -> loss 0.6*4 = 2.4; actual 10/5-11/9 = 35 d -> loss 4.2; deficit 1.8 Gy BED
        self.assertAlmostEqual(r.bed.tumor.prescribed, 60 - 2.4, places=6)
        self.assertEqual(r.bed.total_days, 35)
        self.assertLess(r.extra_dose, 2.0)
        self.assertAlmostEqual(r.dose_per_fx, 2.07, places=2)
        self.assertGreaterEqual(r.bed.tumor.percent_of_rx, 100.0)

    def test_add_fractions_one_missed_day(self):
        r = calculate_bed_makeup(self.inp)
        self.assertLessEqual(r.fractions_to_add, 1)
        self.assertAlmostEqual(r.tumor_pct_required, 100.0, places=1)

    def test_tissue_table_charges_repopulation_once(self):
        r = calculate_bed_dose_makeup(self.inp)
        t = r.bed.tumor
        lq_total = 6 * 2 * 1.2 + 19 * r.dose_per_fx * (1 + r.dose_per_fx / 10)
        self.assertAlmostEqual(t.total, lq_total - 0.6 * (r.bed.total_days - 28), places=6)
        self.assertAlmostEqual(t.before_gap_at_resume + t.after_gap, t.total, places=9)

    def test_inconsistent_planned_end_is_rejected(self):
        inp = self.inp
        inp.planned_last_tx = date(2026, 10, 30)      # only 20 weekdays for 25 fx
        with self.assertRaisesRegex(ValueError, "11/06/2026"):
            calculate_bed_dose_makeup(inp)

    def test_spreadsheet_case_unchanged_when_resumption_is_after_tk(self):
        # Resumption on day 61 > Tk: per-phase and whole-course charging agree.
        inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2018, 12, 12), planned_last_tx=date(2019, 1, 15),
            last_tx_before_gap=date(2019, 1, 11), first_tx_after_gap=date(2019, 2, 11),
            post_dose_per_fx_gy=2, pre_fx=20, tumor=WORKSHEET_TUMOR,
        )
        self.assertEqual(calculate_bed_makeup(inp).fractions_to_add, 8)
        self.assertEqual(calculate_bed_dose_makeup(inp).dose_per_fx, 3.34)


class DefaultsTests(TestCase):
    def test_tumor_default_alpha_beta_is_10(self):
        self.assertEqual(DEFAULT_TUMOR.alpha_beta, 10)
        self.assertEqual(DEFAULT_TUMOR.k, 0.6)
        self.assertEqual(DEFAULT_TUMOR.tk, 28)
        self.assertEqual(WORKSHEET_TUMOR.alpha_beta, 3)

    def test_bed_form_default_tumor_alpha_beta(self):
        from .forms import BEDForm
        self.assertEqual(BEDForm().fields["tumor_alpha_beta"].initial, 10)

    def test_example_with_default_tumor(self):
        inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2018, 12, 12), planned_last_tx=date(2019, 1, 15),
            last_tx_before_gap=date(2019, 1, 11), first_tx_after_gap=date(2019, 2, 11),
            post_dose_per_fx_gy=2, pre_fx=20,
        )
        r = calculate_bed_makeup(inp)
        # a 2 Gy fraction is worth 2.4 Gy BED at a/b = 10, so K = 0.6 costs a quarter fraction per day
        self.assertGreater(r.fractions_to_add, 8)     # a/b 10 makes each 2 Gy fx worth less BED


class PreGapFromCalendarTests(TestCase):
    def test_count_treatment_days(self):
        self.assertEqual(count_treatment_days(date(2019, 2, 11), date(2019, 2, 11), 5), 1)
        self.assertEqual(count_treatment_days(date(2019, 2, 11), date(2019, 2, 15), 5), 5)
        self.assertEqual(count_treatment_days(date(2019, 2, 11), date(2019, 2, 18), 5), 6)
        self.assertEqual(count_treatment_days(date(2019, 2, 9), date(2019, 2, 11), 5), 2)  # Sat start counts
        self.assertEqual(count_treatment_days(date(2019, 2, 11), date(2019, 2, 10), 5), 0)
        # Spreadsheet course: 12/12/2018 - 1/11/2019 is 23 weekdays (20 fx delivered => 3 holidays)
        self.assertEqual(count_treatment_days(date(2018, 12, 12), date(2019, 1, 11), 5), 23)

    def test_pre_gap_derived_when_blank(self):
        inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=60,
            first_tx=date(2026, 3, 2), planned_last_tx=date(2026, 4, 10),     # 30 weekdays, no holidays
            last_tx_before_gap=date(2026, 3, 20), first_tx_after_gap=date(2026, 4, 6),
            post_dose_per_fx_gy=2,
        )
        r = calculate_bed_makeup(inp)
        self.assertEqual(r.pre_fx, 15)
        self.assertTrue(r.pre_fx_from_calendar)
        self.assertEqual(r.pre_dose_per_fx, 2)
        self.assertEqual(r.fx_remaining_planned, 15)
        self.assertFalse(any("skipped" in w for w in r.warnings))
        r2 = calculate_bed_dose_makeup(inp)
        self.assertEqual(r2.pre_fx, 15)
        self.assertEqual(r2.post_fx, 15)

    def test_entered_count_overrides_calendar(self):
        inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2018, 12, 12), planned_last_tx=date(2019, 1, 15),
            last_tx_before_gap=date(2019, 1, 11), first_tx_after_gap=date(2019, 2, 11),
            post_dose_per_fx_gy=2, pre_fx=20, tumor=WORKSHEET_TUMOR,
        )
        r = calculate_bed_makeup(inp)
        self.assertEqual(r.pre_fx, 20)
        self.assertFalse(r.pre_fx_from_calendar)
        self.assertEqual(r.fractions_to_add, 8)

    def test_calendar_count_warns_to_enter_missed_days(self):
        inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2018, 12, 12), planned_last_tx=date(2019, 1, 15),
            last_tx_before_gap=date(2019, 1, 11), first_tx_after_gap=date(2019, 2, 11),
            post_dose_per_fx_gy=2, tumor=WORKSHEET_TUMOR,
        )
        r = calculate_bed_makeup(inp)
        self.assertEqual(r.pre_fx, 23)          # 23 weekdays; the real course had holidays
        self.assertTrue(any("counted from the calendar" in w for w in r.warnings))

    def test_planned_last_date_must_match_prescribed_fractions(self):
        inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=50,
            first_tx=date(2018, 12, 12), planned_last_tx=date(2019, 1, 18),   # 28 weekdays, not 25
            last_tx_before_gap=date(2019, 1, 11), first_tx_after_gap=date(2019, 2, 11),
            post_dose_per_fx_gy=2, pre_fx=20,
        )
        with self.assertRaisesRegex(ValueError, "01/15/2019"):
            calculate_bed_makeup(inp)
        with self.assertRaisesRegex(ValueError, "28 treatment days"):
            calculate_bed_dose_makeup(inp)

    def test_calendar_count_exceeding_rx_raises(self):
        inp = BEDMakeupInput(
            rx_dose_per_fx_gy=2, rx_total_dose_gy=20,       # 10 fx
            first_tx=date(2026, 3, 2), planned_last_tx=date(2026, 3, 13),
            last_tx_before_gap=date(2026, 3, 20), first_tx_after_gap=date(2026, 3, 30),
            post_dose_per_fx_gy=2,
        )
        with self.assertRaises(ValueError):
            calculate_bed_makeup(inp)
        with self.assertRaises(ValueError):
            calculate_bed_dose_makeup(inp)


class TDFEngineTests(TestCase):
    def test_tdf_formula(self):
        # 30 fx x 200 cGy over 40 days: 30 * 200^1.538 * (40/30)^-0.169 / 1000
        expected = 30 * 200 ** 1.538 * (40 / 30) ** -0.169 * 1e-3
        self.assertAlmostEqual(tdf(30, 200, 40), expected, places=6)

    def test_inverse_is_consistent_with_spreadsheet_constant_form(self):
        n, d, t = 12, 200, 16
        target = tdf(n, d, t)
        # Spreadsheet form: 2^(3/1.169)*5^(3/1.169)*T^(0.169/1.169)*TDF^(1/1.169)/d^(1.538/1.169)
        sheet = (2 ** (3 / 1.169) * 5 ** (3 / 1.169) * t ** (0.169 / 1.169)
                 * target ** (1 / 1.169)) / d ** (1.538 / 1.169)
        self.assertAlmostEqual(tdf_fractions_for(target, d, t), sheet, places=6)
        self.assertAlmostEqual(tdf_fractions_for(target, d, t), n, places=6)

    def test_full_case(self):
        inp = TDFInput(
            dose_per_fx_cgy=200, total_dose_cgy=6000,
            start_date=date(2026, 1, 5), original_end_date=date(2026, 2, 13),
            last_tx_before_break=date(2026, 1, 23), first_tx_after_break=date(2026, 2, 9),
            proposed_finish_date=date(2026, 3, 6),
            fx_treated_before_break=15,
        )
        r = calculate_tdf(inp)
        self.assertEqual(r.total_fx, 30)
        self.assertEqual(r.initial_phase_days, 18)
        self.assertEqual(r.rest_days, 17)
        self.assertEqual(r.post_break_days, 25)
        # Cell-by-cell replication
        tdf_tot = 30 * 200 ** 1.538 * (39 / 30) ** -0.169 * 1e-3
        tdf1 = 15 * 200 ** 1.538 * (18 / 15) ** -0.169 * 1e-3
        tdf_arp = tdf1 * (18 / (18 + 17)) ** 0.11
        tdf2 = tdf_tot - tdf_arp
        add = ((2 ** (3 / 1.169) * 5 ** (3 / 1.169) * 25 ** (0.169 / 1.169)
                * tdf2 ** (1 / 1.169)) / 200 ** (1.538 / 1.169)) - (30 - 15)
        self.assertAlmostEqual(r.tdf_total, tdf_tot, places=6)
        self.assertAlmostEqual(r.tdf_arp, tdf_arp, places=6)
        self.assertAlmostEqual(r.fractions_to_add, add, places=6)
        self.assertGreater(r.fractions_to_add, 0)


class TDFDerivedDatesTests(TestCase):
    def test_dates_are_derived_when_omitted(self):
        inp = TDFInput(
            dose_per_fx_cgy=200, total_dose_cgy=6000,
            start_date=date(2026, 1, 5),
            last_tx_before_break=date(2026, 1, 23), first_tx_after_break=date(2026, 2, 9),
            fx_treated_before_break=15,
        )
        r = calculate_tdf(inp)
        self.assertEqual(r.original_end_date, date(2026, 2, 13))      # 30 fx Mon-Fri from 1/5
        self.assertTrue(r.finish_date_derived)
        # 16 post-break fx from Mon 2/9 end Mon 3/2 (21 d); TDF asks for 15.91 over that span.
        self.assertEqual(r.post_fx_whole, 16)
        self.assertEqual(r.proposed_finish_date, date(2026, 3, 2))
        self.assertEqual(r.post_break_days, 21)
        self.assertAlmostEqual(r.fx_required_after_break, 15.91, places=2)
        self.assertEqual(r.fractions_to_add_rounded, 1)
        # self-consistency: the solved whole count is the nearest whole number to the continuous one
        self.assertEqual(round(r.fx_required_after_break), r.post_fx_whole)

    def test_fx_before_break_counted_from_calendar(self):
        inp = TDFInput(
            dose_per_fx_cgy=200, total_dose_cgy=6000,
            start_date=date(2026, 1, 5),
            last_tx_before_break=date(2026, 1, 23), first_tx_after_break=date(2026, 2, 9),
        )
        r = calculate_tdf(inp)
        self.assertEqual(r.fx_treated_before_break, 15)
        self.assertTrue(r.fx_treated_from_calendar)
        self.assertTrue(any("counted from the calendar" in w for w in r.warnings))
        bid = calculate_tdf(TDFInput(
            dose_per_fx_cgy=150, total_dose_cgy=6000, fx_per_day=2,
            start_date=date(2026, 1, 5),
            last_tx_before_break=date(2026, 1, 9), first_tx_after_break=date(2026, 1, 19),
        ))
        self.assertEqual(bid.fx_treated_before_break, 10)      # 5 treatment days x 2

    def test_explicit_dates_still_honoured(self):
        inp = TDFInput(
            dose_per_fx_cgy=200, total_dose_cgy=6000,
            start_date=date(2026, 1, 5), original_end_date=date(2026, 2, 13),
            last_tx_before_break=date(2026, 1, 23), first_tx_after_break=date(2026, 2, 9),
            proposed_finish_date=date(2026, 3, 6), fx_treated_before_break=15,
        )
        r = calculate_tdf(inp)
        self.assertFalse(r.finish_date_derived)
        self.assertEqual(r.post_break_days, 25)


class ViewTests(TestCase):
    def test_pages_render(self):
        for name in ["home", "tdf", "bed", "reference", "rule_of_thumb"]:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)

    def test_bed_post(self):
        r = self.client.post(reverse("bed"), {
            "mode": "fractions-1",
            "rx_dose_per_fx_gy": "2", "rx_total_dose_gy": "50",
            "first_tx": "2018-12-12", "planned_last_tx": "2019-01-15",
            "last_tx_before_gap": "2019-01-11", "first_tx_after_gap": "2019-02-11",
            "pre_fx": "20",
            "tumor_alpha_beta": "3", "tumor_k": "0.6", "tumor_tk": "28",
            "early_alpha_beta": "10", "early_k": "0.25", "early_tk": "0",
            "late_alpha_beta": "2.5", "late_k": "0", "late_tk": "0",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Fractions to add")
        self.assertContains(r, "+8")
        self.assertContains(r, "2/27/2019")
        self.assertContains(r, "Nearest whole-fraction options")
        self.assertContains(r, "treatment gap of 31 days")
        self.assertContains(r, "All options")
        self.assertContains(r, "3.27 Gy BID")
        self.assertNotContains(r, "Add fractions, BID")
        self.assertNotContains(r, "proposed_final_tx")

    def _post(self, **over):
        data = {
            "mode": "fractions-1",
            "rx_dose_per_fx_gy": "2", "rx_total_dose_gy": "50",
            "first_tx": "2018-12-12", "planned_last_tx": "2019-01-15",
            "last_tx_before_gap": "2019-01-11", "first_tx_after_gap": "2019-02-11",
            "pre_fx": "20",
            "tumor_alpha_beta": "3", "tumor_k": "0.6", "tumor_tk": "28",
            "early_alpha_beta": "10", "early_k": "0.25", "early_tk": "0",
            "late_alpha_beta": "2.5", "late_k": "0", "late_tk": "0",
        }
        data.update(over)
        return self.client.post(reverse("bed"), data)

    def test_bed_add_fractions_bid_not_offered(self):
        r = self._post(mode="fractions-2")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Select a valid choice")

    def test_bed_dose_bid_post(self):
        r = self._post(mode="dose-2")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "3.27")
        self.assertContains(r, "2/13/2019")
        self.assertContains(r, "no extra treatment days · BID")

    def test_bed_page_lists_four_calculations(self):
        r = self.client.get(reverse("bed"))
        self.assertContains(r, '<select name="mode"')
        for v in ("fractions-1", "dose-1", "dose-2"):
            self.assertContains(r, f'<option value="{v}"')
        self.assertNotContains(r, '<option value="fractions-2"')
        self.assertNotContains(r, "id_fx_per_day")
        self.assertNotContains(r, "id_post_dose_per_fx_gy")
        self.assertNotContains(r, "id_fx_per_week")

    def test_bed_dose_mode_post(self):
        r = self.client.post(reverse("bed"), {
            "mode": "dose-1",
            "rx_dose_per_fx_gy": "2", "rx_total_dose_gy": "50",
            "first_tx": "2018-12-12", "planned_last_tx": "2019-01-15",
            "last_tx_before_gap": "2019-01-11", "first_tx_after_gap": "2019-02-11",
            "pre_fx": "20",
            "tumor_alpha_beta": "3", "tumor_k": "0.6", "tumor_tk": "28",
            "early_alpha_beta": "10", "early_k": "0.25", "early_tk": "0",
            "late_alpha_beta": "2.5", "late_k": "0", "late_tk": "0",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Dose per fraction")
        self.assertContains(r, "3.34")
        self.assertContains(r, "no extra treatment days")
        self.assertContains(r, "2/15/2019")

    def test_bed_planned_end_is_derived_not_typed(self):
        # Whatever is posted for the planned last date, the prescribed fraction count decides it.
        r = self._post(planned_last_tx="2019-01-18")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "All options")
        self.assertContains(r, "+8")                   # same answer as with 2019-01-15
        r = self._post(planned_last_tx="")
        self.assertContains(r, "+8")

    def test_bed_rejects_non_integer_fraction_count(self):
        r = self._post(rx_total_dose_gy="51")
        self.assertContains(r, "must be a whole number of fractions")
        self.assertNotContains(r, "All options")

    def test_bed_rejects_weekend_dates(self):
        r = self._post(first_tx_after_gap="2019-02-09")      # a Saturday
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Saturday")
        self.assertContains(r, "not treated on weekends")
        self.assertNotContains(r, "All options")

    def test_bed_rejects_pre_fx_at_or_above_rx(self):
        r = self.client.post(reverse("bed"), {
            "rx_dose_per_fx_gy": "2", "rx_total_dose_gy": "50",
            "first_tx": "2018-12-12", "planned_last_tx": "2019-01-15",
            "last_tx_before_gap": "2019-01-11", "first_tx_after_gap": "2019-02-11",
            "pre_fx": "25",
            "tumor_alpha_beta": "3", "tumor_k": "0.6", "tumor_tk": "28",
            "early_alpha_beta": "10", "early_k": "0.25", "early_tk": "0",
            "late_alpha_beta": "2.5", "late_k": "0", "late_tk": "0",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "fewer than the prescribed fraction count")

    def test_tdf_post(self):
        r = self.client.post(reverse("tdf"), {
            "dose_per_fx_gy": "2", "total_dose_gy": "60",
            "fx_per_day": "1", "fx_per_week": "5",
            "start_date": "2026-01-05",
            "last_tx_before_break": "2026-01-23", "first_tx_after_break": "2026-02-09",
            "fx_treated_before_break": "",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Fractions to add")
        self.assertContains(r, "Finish date")
        self.assertContains(r, "3/2/2026")            # 16 post-break fx Mon-Fri from 2/9
        self.assertContains(r, "2/13/2026")           # derived planned end: 30 fx from 1/5
        self.assertContains(r, "15 fx over 18 days (counted from calendar)")   # 1/5..1/23 = 15 weekdays
        self.assertNotContains(r, "id_proposed_finish_date")
