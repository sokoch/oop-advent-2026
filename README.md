# OOP with Python — Assignment 2 (Advent 2026)

Two mini-projects submitted as one repository.

## Project 1 — Weather Forecast Program

Takes a location name, geocodes it via the Open-Meteo Geocoding API,
fetches a daily forecast from Open-Meteo and MET Norway, and displays it
as a text summary, a formatted table, and a chart of temperature range
with precipitation.

**Notebook:** `notebooks/project1_weather.ipynb`
**Reusable logic:** `src/weather_api.py`, `src/weather_parsing.py`
**Tests:** `tests/test_weather_parsing.py` (13 tests, all offline)

### Setup

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### Contact email for API User-Agent

MET Norway requires an identifying contact in the `User-Agent` header.
Set it via environment variable before running the notebook:

```bash
export CONTACT_EMAIL="b40822@students.ucu.ac.ug"
export USER_AGENT="OOP-Advent-2026/1.0 (b40822@students.ucu.ac.ug)"
```

### Run

```bash
jupyter lab notebooks/project1_weather.ipynb
```

### Key findings

- `urllib` and `requests` produce identical responses; `requests` is
  shorter and safer for JSON APIs.
- Open-Meteo returned a transient HTTP 503 during development; our retry
  wrapper handled it with exponential backoff and later recovered.
- MET Norway rejects requests with placeholder emails (HTTP 403).
  Fixed by reading `USER_AGENT` from env on every call.
- The two forecast sources differ by a few °C on average
  (mean diff −0.51 °C Tmax, +0.64 °C Tmin in our sample).

---

## Project 2 — Uganda Tax Calculator

An OOP tax engine implementing PAYE (resident and non-resident), NSSF,
Local Service Tax, VAT, and Corporate Income Tax, plus a `TaxPlanner`
extension that compares salary vs. dividend scenarios.

**Notebook:** `notebooks/project2_tax.ipynb`
**Reusable logic:** `src/money.py`, `src/taxes.py`, `src/payroll.py`
**Tests:** `tests/test_money.py` (25 tests), `tests/test_taxes.py` (35 tests)

### Design highlights

- `Money` — a `Decimal`-only value object in UGX with `__add__`,
  `__sub__`, `__mul__`, `__lt__`, `__le__`, `__eq__`, `__hash__`,
  `__repr__`, `__str__`. Rejects floats at the API boundary.
- `Tax` — abstract base class with a single `calculate(ctx)` contract.
- `TaxContext` / `TaxResult` — frozen dataclasses separating inputs
  from outputs.
- `Band` / `BandSchedule` — bands as data; a new schedule is a new
  instance, not a code change.
- `PayrollPipeline` — composition, not inheritance. Wires LST → NSSF
  employee → PAYE → net.
- `TaxPlanner` — the extension task; composes PAYE and CIT.

### Run

```bash
jupyter lab notebooks/project2_tax.ipynb
```

### Key findings

- `Decimal` is essential for tax: `0.1 + 0.2 != 0.3` in float, and the
  error compounds across bands. `Money` refuses floats to make the
  engine safe by construction.
- Progressive PAYE produces 2,888,250 UGX at exactly 10,000,000 UGX
  monthly; the 10% surcharge becomes visible only once the excess above
  10,000,000 crosses 5 shillings (rounding HALF_UP).
- Inheritance is justified for the `Tax` ABC; composition is the right
  tool for `PayrollPipeline` and `TaxPlanner`.
- 60 tests cover money and tax logic, including Hypothesis property
  tests (tax non-negative, monotonic, never exceeds income) and
  boundary tests at every band edge from the assignment brief.

---

## Running the Tests

```bash
pytest -q
# 73 passed in ~1 second
```

All tests run offline using saved fixtures in `tests/data/`.

---

## AI-use declaration

This repository was developed with assistance from an AI coding
assistant. Specifically:

- The AI proposed initial code structure, docstrings, and test cases.
- Every line was reviewed and understood by the author before commit.
- Boundary values and tax rules were cross-checked against the
  assignment brief. Production use would require verifying against the
  current URA notice.
- The author is prepared to explain any line of code in a viva.

---

## Data sources & licences

- **Open-Meteo** (geocoding + forecast) — CC-BY 4.0.
- **MET Norway Locationforecast 2.0** — NLOD 2.0 / CC-BY 4.0.
- **URA PAYE schedule** — the bands in `src/taxes.py` are those from the
  Assignment 2 brief. Current implementation uses a placeholder
  `as_of` date of 2024-07-01; production use must cite the specific
  URA notice and its effective date.

---

## Repository structure

```
.
├── config/
│   └── config.example.py
├── notebooks/
│   ├── project1_weather.ipynb
│   └── project2_tax.ipynb
├── src/
│   ├── exceptions.py
│   ├── money.py
│   ├── payroll.py
│   ├── taxes.py
│   ├── weather_api.py
│   └── weather_parsing.py
├── tests/
│   ├── data/
│   ├── test_money.py
│   ├── test_taxes.py
│   └── test_weather_parsing.py
├── .gitignore
├── README.md
└── requirements.txt
```
