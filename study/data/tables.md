### Table 1 — what each prompt bought, over all 26 tasks

| id | treatment | n | routes tier 1 | **runs on tier 1** | of the 23 feasible | correct | MISMATCH |
|---|---|---:|---:|---:|---:|---:|---:|
| T0 | control | 104 | 62.5% | **66.3%** | 75.0% | 100.0% | 0 |
| T1 | nudge | 104 | 75.0% | **76.9%** | 87.0% | 100.0% | 0 |
| T2 | runtime-aware | 104 | 96.2% | **88.5%** | 100.0% | 100.0% | 0 |
| T3 | skill | 104 | 77.9% | **81.7%** | 92.4% | 100.0% | 0 |
| T4 | capability-brief | 104 | 93.3% | **89.4%** | 100.0% | 100.0% | 0 |
| T5 | cookbook | 104 | 91.3% | **88.5%** | 100.0% | 100.0% | 0 |
| T6 | brief+cookbook | 104 | 93.3% | **88.5%** | 100.0% | 100.0% | 0 |
| T7 | verify-once | 78 | 97.4% | **89.7%** | 100.0% | 100.0% | 0 |
| T8 | verify-loop | 78 | 94.9% | **88.5%** | 100.0% | 100.0% | 0 |

### Table 2 — spread across independent replicates

| id | treatment | replicates | tier-1 rate per replicate | mean | spread |
|---|---|---:|---|---:|---:|
| T0 | control | 4 | 65.4% · 65.4% · 65.4% · 69.2% | 66.3% | 3.8 pp |
| T1 | nudge | 4 | 73.1% · 76.9% · 84.6% · 73.1% | 76.9% | 11.5 pp |
| T2 | runtime-aware | 4 | 88.5% · 88.5% · 88.5% · 88.5% | 88.5% | 0.0 pp |
| T3 | skill | 4 | 84.6% · 80.8% · 80.8% · 80.8% | 81.7% | 3.8 pp |
| T4 | capability-brief | 4 | 88.5% · 92.3% · 88.5% · 88.5% | 89.4% | 3.8 pp |
| T5 | cookbook | 4 | 88.5% · 88.5% · 88.5% · 88.5% | 88.5% | 0.0 pp |
| T6 | brief+cookbook | 4 | 88.5% · 88.5% · 88.5% · 88.5% | 88.5% | 0.0 pp |
| T7 | verify-once | 3 | 88.5% · 92.3% · 88.5% | 89.7% | 3.8 pp |
| T8 | verify-loop | 3 | 88.5% · 88.5% · 88.5% | 88.5% | 0.0 pp |

### Table 3 — what stopped the programs that did not reach tier 1

| id | treatment | the three commonest blockers |
|---|---|---|
| T0 | control | `run: module` ×31, `run: os-listdir` ×4 |
| T1 | nudge | `run: module` ×18, `run: os-listdir` ×4, `run: bigint` ×2 |
| T2 | runtime-aware | `run: bigint` ×4, `run: os-listdir` ×4, `run: module` ×4 |
| T3 | skill | `run: module` ×12, `run: os-listdir` ×4, `run: dict-method` ×3 |
| T4 | capability-brief | `run: os-listdir` ×4, `run: module` ×4, `run: bigint` ×3 |
| T5 | cookbook | `run: bigint` ×4, `run: os-listdir` ×4, `run: module` ×4 |
| T6 | brief+cookbook | `run: bigint` ×4, `run: os-listdir` ×4, `run: module` ×4 |
| T7 | verify-once | `run: bigint` ×3, `run: os-listdir` ×3, `run: module` ×2 |
| T8 | verify-loop | `run: module` ×4, `run: bigint` ×3, `run: os-listdir` ×2 |

### Table 4 — per task: which ones prompting could move

| task | tempts | tier-1 feasible | control | cheapest static prompt that gets it | engine in the loop |
|---|---|---|---:|---:|---:|
| `sum-evens` | - | yes | 100.0% | T1 (100%) | 100.0% |
| `wc-lines` | - | yes | 100.0% | T1 (100%) | 100.0% |
| `word-freq-top3` | collections.Counter | yes | 0.0% | T1 (100%) | 100.0% |
| `json-field` | - | yes | 100.0% | T1 (100%) | 100.0% |
| `json-sum-field` | - | yes | 100.0% | T1 (100%) | 100.0% |
| `json-roundtrip` | - | yes | 100.0% | T1 (100%) | 100.0% |
| `json-nested-path` | - | yes | 100.0% | T1 (100%) | 100.0% |
| `file-write-size` | pathlib | yes | 100.0% | T1 (100%) | 100.0% |
| `csv-column-sum` | csv | yes | 0.0% | T2 (100%) | 100.0% |
| `csv-group-max` | csv, collections | yes | 0.0% | T2 (100%) | 100.0% |
| `extract-ints` | re | yes | 0.0% | T2 (100%) | 100.0% |
| `squeeze-space` | re | yes | 100.0% | T1 (100%) | 100.0% |
| `strip-comments` | re | yes | 100.0% | T1 (100%) | 100.0% |
| `sort-by-length` | - | yes | 100.0% | T1 (100%) | 100.0% |
| `dedupe-order` | dict.fromkeys | yes | 100.0% | T1 (100%) | 100.0% |
| `unique-sorted` | set printing | yes | 100.0% | T1 (100%) | 100.0% |
| `char-histogram` | collections.Counter | yes | 0.0% | T1 (100%) | 100.0% |
| `align-columns` | str.center, textwrap | yes | 100.0% | T1 (100%) | 100.0% |
| `money-format` | - | yes | 100.0% | T1 (100%) | 100.0% |
| `duration-breakdown` | datetime.timedelta | yes | 100.0% | T1 (100%) | 100.0% |
| `isqrt` | math.isqrt | yes | 25.0% | T2 (100%) | 100.0% |
| `basename-noext` | pathlib | yes | 100.0% | T1 (100%) | 100.0% |
| `listdir-filter` | glob, pathlib | **no** | 0.0% | T1 (0%) | 0.0% |
| `hex-of-file` | binascii | yes | 100.0% | T1 (100%) | 100.0% |
| `big-factorial` | - | **no** | 0.0% | T4 (25%) | 0.0% |
| `sha256-abc` | hashlib | **no** | 0.0% | T1 (0%) | 16.7% |

