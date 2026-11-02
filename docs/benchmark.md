# Sabi accuracy benchmark

Run on 31 October 2026 with `meta-models/muse-spark-open-1026`, prompt version from `app/agent/orchestrator.py`.
64 real-style trader messages (English, Pidgin, Yoruba, Hausa, 8 French, 2 Portuguese, 2 Swahili), each against a fresh copy of the demo shop.

**63 of 64 messages fully right (98%).** A message counts only if every field of every entry is right.

Reply time: median 5.3 s, slowest 21.7 s.

| Field | Right | Checked | % |
|---|---:|---:|---:|
| kind | 65 | 65 | 100% |
| cash/bank | 50 | 51 | 98% |
| items | 43 | 43 | 100% |
| paid | 39 | 39 | 100% |
| person | 32 | 32 | 100% |
| amount | 22 | 22 | 100% |
| price | 13 | 13 | 100% |
| replied in her language | 12 | 12 | 100% |
| expense type | 8 | 8 | 100% |
| asked first | 1 | 1 | 100% |

Wrong fields never reach the books unchecked: code validates every proposal, and anything unusual is asked before it is recorded. This measures how often the owner has to correct or answer Sabi.

## By language

| Language | Fully right | Messages |
|---|---:|---:|
| English, Pidgin, Nigerian | 51 | 52 |
| French | 8 | 8 |
| Portuguese | 2 | 2 |
| Swahili | 2 | 2 |

## Misses

- “I take 10k from the shop money for house”: wrong cash/bank

Cases: `tests/bench/cases.json`. Run again: `python scripts/benchmark.py`.
