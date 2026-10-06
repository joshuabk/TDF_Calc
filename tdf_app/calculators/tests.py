from datetime import date

from django.test import TestCase
from django.urls import reverse

from .engine import (
    BEDInput, TDFInput, calculate_bed, calculate_tdf, tdf, tdf_fractions_for,
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


class ViewTests(TestCase):
    def test_pages_render(self):
        for name in ["home", "tdf", "bed", "reference", "rule_of_thumb"]:
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 200, name)

    def test_bed_post(self):
        r = self.client.post(reverse("bed"), {
            "rx_dose_per_fx_gy": "2", "rx_total_dose_gy": "50",
            "first_tx": "2018-12-12", "planned_last_tx": "2019-01-18",
            "last_tx_before_gap": "2019-01-11", "first_tx_after_gap": "2019-02-11",
            "proposed_final_tx": "2019-02-15",
            "pre_dose_per_fx_gy": "2", "pre_fx": "20",
            "post_dose_per_fx_gy": "2", "post_fx": "5",
            "tumor_alpha_beta": "3", "tumor_k": "0.6", "tumor_tk": "28",
            "early_alpha_beta": "10", "early_k": "0.25", "early_tk": "0",
            "late_alpha_beta": "2.5", "late_k": "0", "late_tk": "0",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "78.4")
        self.assertContains(r, "treatment gap of 31 days")

    def test_tdf_post(self):
        r = self.client.post(reverse("tdf"), {
            "dose_per_fx_cgy": "200", "total_dose_cgy": "6000",
            "fx_per_day": "1", "fx_per_week": "5",
            "start_date": "2026-01-05", "original_end_date": "2026-02-13",
            "last_tx_before_break": "2026-01-23", "first_tx_after_break": "2026-02-09",
            "proposed_finish_date": "2026-03-06", "fx_treated_before_break": "15",
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Fractions to add")
