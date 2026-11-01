# Company data from ARES (CIPW-44)

Fides looks the company up by the IČO of the statement in **ARES**, the public register of economic subjects of the Ministry of Finance. It gets the name, legal form, registered address, date of foundation, the predominant activity (**CZ-NACE**, needed for the industry benchmarks of CIPW-14) and the employee band. The `company` stage of the pipeline writes them to `Result.company`. The app shows the name and industry in the result header and everything else in *About this result*.

## Where the data comes from

| Order | Source | When |
| --- | --- | --- |
| 1 | saved answer in `outputs/cache/ares/{ico}.json` | the company was asked before |
| 2 | ARES REST API: `ekonomicke-subjekty/{ico}` (register), `ekonomicke-subjekty-res/{ico}` (CZ-NACE and size, statistical register of ČSÚ) | `ares.enabled` and not `offline`; the answer is saved |
| 3 | `data/companies.csv`, the snapshot of every company of our dataset | no network: offline mode, ARES switched off, a closed network, ARES down or slower than `ares.timeout` |

With none of them the company stays unknown: the step's note says why, and the result is complete otherwise. Only the IČO is sent to ARES. Names of codes (CZ-NACE, legal forms, employee bands) come from `config/ares_codebooks.json`, downloaded from the same API, so they also work offline.

## Checks against the statement

| Finding | Warning |
| --- | --- |
| IČO with a wrong check digit (weights 8…2, mod 11) | `company: IČO … has a wrong check digit (misread, or a typo in the file name); enter the right IČO`. Nothing is sent to ARES |
| IČO that ARES does not know | `company: IČO … is not in ARES; enter the right IČO` |
| The statement prints another name than ARES has for the IČO | `company: the statement names '…', ARES '…' for IČO …; check the IČO` |

Names are compared without diacritics, punctuation and legal form (`a.s.`, `s.r.o.`, `spol. s r.o.` …). They differ when their rapidfuzz `token_set_ratio` is below 60. A misread IČO belongs to an unrelated company and scores far lower. OCR noise in a printed name stays above 70 ("Morxfi Štětí a.s" against "Mondi Štětí a.s.": 78). Of the 6 statements in our corpus whose name the parser reads, none is flagged.

The check digit found three typos in our dataset:

| File | IČO in the file name | Real IČO |
| --- | --- | --- |
| `25556149_2011_statement.pdf` | 25556149 | PLOMA, a.s. 25556045, corrected in `data/index.csv` |
| `27958331_*` | 27958331 | Czech Games Edition s.r.o. 27920992, which the parser reads from the statement |
| `29048579_Workswell_Financial-Report_2024.pdf` | 29048579 | Workswell s.r.o. 29048575 |

## Keeping the snapshot current

```
python scripts/ares_lookup.py               # every company in data/index.csv and data/raw/, then data/companies.csv
python scripts/ares_lookup.py 25556045      # add one company
python scripts/ares_lookup.py --refresh     # ask ARES again instead of using the saved answers
python scripts/ares_lookup.py --codebooks   # download config/ares_codebooks.json again
```

The snapshot holds all 79 companies of the dataset, every one with its CZ-NACE (`tests/test_ares.py` checks it for `data/index.csv`).

## Settings

`ares.enabled` (true), `ares.url`, `ares.timeout` (5 s), `ares.cache` (`''` = `outputs/cache/ares`); `offline: true` also stops the lookup. Tests never use the network: `tests/conftest.py` switches ARES off and gives the saved answers a temporary folder, and `tests/test_ares.py` replays a recorded answer.

## CZ-NACE versions

ARES gives the predominant activity in the CZ-NACE classification valid since 2025 (`nace`, named from the codebook with its section) and in CZ-NACE 2008 (`nace_2008`). Statistics for years before 2025, such as industry benchmarks, mostly use the 2008 codes.
