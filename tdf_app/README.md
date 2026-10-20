# Treatment Interruption Calculator — Northside Hospital Radiation Oncology

Django web application that replaces the departmental *Treatment Interruption Worksheet.xlsx*.
It performs TDF and BED (linear-quadratic) compensation calculations for missed or interrupted
radiation treatments. The app is **stateless**: no patient data is stored, so there is no PHI at rest.

## Features

| Page | Spreadsheet tab | Description |
|---|---|---|
| `/tdf/` | TDF | Orton–Ellis TDF: TDF lost during a rest period and the number of fractions to add at the same dose/fx. Doses entered in Gy. Fractions treated before the break are counted from the calendar (start through last treatment before the break, × fx/day) unless entered. The original planned end date is derived (last prescribed fraction at the fx/week and fx/day entered) and the finish date is solved for: the date of the last post-break fraction for the whole number of fractions the TDF equation calls for over that span, iterated to self-consistency. |
| `/bed/` | BED | LQ model with repopulation. Treatment is assumed Mon–Fri. The planned last treatment date is derived, not typed: the prescribed number of fractions Mon–Fri from the first treatment (filled live in the browser, recomputed server-side; the engine still asserts it). Fractions delivered before the gap are counted from the calendar (first treatment through last treatment before the gap; dose/fx = prescription) unless entered as an override; a warning flags courses whose planned span contains skipped days. Two make-up strategies: (a) add fractions at the prescription dose/fx, reporting fractions to add, additional dose and the resulting finish date; (b) keep the fractions still owed and raise the dose per fraction (closed-form, rounded up to the cGy) so no treatment days are added; (c) BID — keep the fractions still owed, give them 2 fx/day (≥ 6 h apart) and solve the dose per fraction (adding fractions BID is deliberately not offered); an "All options" table shows the three side by side; then shows tumor, early- and late-responding normal tissue BED, % of prescription, and the narrative summary sentence. Tumor presets from the α/β sheet. |
| `/reference/` | Alpha-Beta | Model parameters and normal-tissue tolerance table. |
| `/rule-of-thumb/` | Quick Rule of Thumb | Dose to add per missed fraction. |

All formulas are ported cell-for-cell in `calculators/engine.py`; `calculators/tests.py` checks the
engine against values hand-computed from the spreadsheet's example (e.g. Tumor BED 78.4 % of Rx).

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py test          # 12 tests
python manage.py runserver
```

Open http://127.0.0.1:8000/. No database migrations are required (the app uses no models), though
`python manage.py migrate` is harmless if you want the default Django tables.

## Deploy (intranet)

Set environment variables and serve with any WSGI server behind the hospital reverse proxy:

```bash
export DJANGO_SECRET_KEY='<long random string>'
export DJANGO_DEBUG=0
export DJANGO_ALLOWED_HOSTS='tdf.northside.local,10.0.0.12'
pip install gunicorn
python manage.py collectstatic --noinput
gunicorn config.wsgi:application --bind 0.0.0.0:8000
```

Serve `staticfiles/` from the proxy (or add WhiteNoise). Because nothing is persisted, the app can run
as a single container with no volume.

## Project layout

```
config/            Django settings / URLs / WSGI
calculators/
  engine.py        Pure-Python TDF and BED formulas + reference tables (no Django dependency)
  forms.py         Input validation (date ordering, fraction counts)
  views.py         Thin views: form -> engine -> template
  templates/       base layout, calculators, reference pages (print-friendly)
  static/          app.css — clinical light theme
  tests.py         Engine regression tests against spreadsheet values + view tests
```

## Clinical notes

- TDF: `TDF = N·d^1.538·(T/N)^-0.169·10^-3` (d in cGy; the page takes Gy and converts), rest-period decay `(T1/(T1+R))^0.11`.
- BED make-up solver: post-gap fractions N stepped up from 0 until tumor BED(before gap, at resumption) + BED(after gap) ≥ BED(prescribed); the finish date for each N is the date of the N-th fraction at the chosen fx/week, so added fractions also add repopulation days. Fractions to add = N − (Rx fx − delivered fx).
- BED dose-per-fraction solver: N fixed at Rx fx − delivered fx, T_post fixed by fx/week; d solves N·d·(1+d/(α/β)) = BED(prescribed) − BED(before gap, at resumption) + K·T_post, rounded up to 0.01 Gy.
- BED: `N·d·(1+d/(α/β)) − K·max(0, T − Tk)`, with the repopulation term charged ONCE on the overall course (first to last fraction) — not per phase. (The worksheet's post-gap cells charged K × post-gap days with no Tk offset, which over-charges by K·(Tk − resumption day) for an early gap.) The tumor term applies repopulation only when `T ≥ Tk`,
  matching the spreadsheet's `IF(T<Tk, …)`.
- Results are decision support only and must be reviewed by the attending radiation oncologist.
