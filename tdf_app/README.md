# Treatment Interruption Calculator — Northside Hospital Radiation Oncology

Django web application that replaces the departmental *Treatment Interruption Worksheet.xlsx*.
It performs TDF and BED (linear-quadratic) compensation calculations for missed or interrupted
radiation treatments. The app is **stateless**: no patient data is stored, so there is no PHI at rest.

## Features

| Page | Spreadsheet tab | Description |
|---|---|---|
| `/tdf/` | TDF | Orton–Ellis TDF: TDF lost during a rest period and the number of fractions to add at the same dose/fx. |
| `/bed/` | BED | LQ model with repopulation: tumor, early- and late-responding normal tissue BED before/after a gap, % of prescription, and the narrative summary sentence. Tumor presets from the α/β sheet. |
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

- TDF: `TDF = N·d^1.538·(T/N)^-0.169·10^-3` (d in cGy), rest-period decay `(T1/(T1+R))^0.11`.
- BED: `N·d·(1+d/(α/β)) − K·(T − Tk)`; the tumor term applies repopulation only when `T ≥ Tk`,
  matching the spreadsheet's `IF(T<Tk, …)`.
- Results are decision support only and must be reviewed by the attending radiation oncologist.
