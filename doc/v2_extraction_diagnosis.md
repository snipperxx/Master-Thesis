# v2 extraction diagnosis — qwen3.5:4b

_Snapshot 2026-06-13, taken **while the 52-doc job was still running**. 50 readable docs._

## Headline

**It is mostly NOT garbage.** Coverage is healthy on most docs (median **14** facts/doc; only **1/50** cover < 50% of their sections). The facts that exist have valid offsets (`anchor=100`) and highlight correctly. The "big text, one sentence annotated" impression comes from a few specific places, not the whole run:

1. **You were viewing docs mid-run.** The job rewrites each facts file **non-atomically**, so opening a document while its file is being (re)written shows it half-empty or blank. Several files I sampled (e.g. `train-000000, train-000004`) were unreadable purely because they were being written as I read them. **Re-check after the job finishes.**
2. **The uploaded doc got 0 facts** (`user-7c7f8fd580`): 3 short recitals, nothing extracted — worth a closer look (user-doc structure or v2 dropping short recitals).
3. **Table / annex docs are mangled** (e.g. `train-000016`): the model fuses several table cells into one over-long subject — a table-linearisation problem, independent of v2.
4. **Short / procedural docs sit around 50%** (`train-000121`, `train-000016`, `train-000036`): v2's five *"drop the fact / return `{"facts":[]}`"* rules (rules 2, 7, 8, 11 + header) make the 4B model bail on sections it can't fully decontextualise. This is a real **guideline-complexity-vs-model-capability** effect — useful for the thesis, not just a bug.

Minor: the ~1960-token v2 prompt eats ~half of `num_ctx=4096` — tight for single-giant-article docs, but not the main driver.

_Files unreadable at snapshot time (mid-write): train-000000, train-000004 — transient._

## Per-doc coverage (worst first)

| doc | facts | covered / available sections | coverage |
|---|--:|:--:|:--:|
| user-7c7f8fd580 ⚠️ | 0 | 0 / 3 | 0% |
| train-000121 | 3 | 1 / 2 | 50% |
| train-000016 | 6 | 1 / 2 | 50% |
| train-000036 | 23 | 4 / 8 | 50% |
| train-000130 | 24 | 5 / 9 | 56% |
| train-000146 | 62 | 15 / 26 | 58% |
| train-000005 | 9 | 5 / 8 | 62% |
| train-000085 | 17 | 5 / 8 | 62% |
| train-000041 | 13 | 7 / 11 | 64% |
| train-000052 | 15 | 7 / 11 | 64% |
| train-000100 | 16 | 7 / 11 | 64% |
| train-000035 | 4 | 2 / 3 | 67% |
| train-000119 | 10 | 6 / 9 | 67% |
| train-000058 | 14 | 8 / 12 | 67% |
| train-000140 | 14 | 4 / 6 | 67% |
| train-000027 | 15 | 8 / 12 | 67% |
| train-000150 | 11 | 7 / 10 | 70% |
| train-000069 | 10 | 5 / 7 | 71% |
| train-000006 | 7 | 6 / 8 | 75% |
| train-000049 | 16 | 9 / 12 | 75% |
| train-000149 | 37 | 9 / 12 | 75% |
| train-000112 | 18 | 10 / 13 | 77% |
| train-000054 | 40 | 11 / 14 | 79% |
| train-000076 | 6 | 4 / 5 | 80% |
| train-000037 | 11 | 5 / 6 | 83% |
| train-000111 | 30 | 10 / 12 | 83% |
| train-000072 | 14 | 6 / 7 | 86% |
| train-000013 | 58 | 15 / 17 | 88% |
| train-000090 | 13 | 8 / 9 | 89% |
| train-000067 | 51 | 25 / 28 | 89% |
| train-000063 | 5 | 2 / 2 | 100% |
| train-000148 | 5 | 2 / 2 | 100% |
| train-000143 | 6 | 3 / 3 | 100% |
| train-000019 | 7 | 3 / 3 | 100% |
| train-000070 | 7 | 3 / 3 | 100% |
| train-000116 | 7 | 4 / 4 | 100% |
| train-000124 | 7 | 4 / 4 | 100% |
| train-000137 | 7 | 4 / 4 | 100% |
| train-000061 | 8 | 5 / 5 | 100% |
| user-a60bde451b | 8 | 5 / 5 | 100% |
| train-000093 | 12 | 2 / 2 | 100% |
| train-000096 | 14 | 3 / 3 | 100% |
| train-000007 | 18 | 6 / 6 | 100% |
| train-000095 | 18 | 2 / 2 | 100% |
| train-000125 | 19 | 8 / 8 | 100% |
| train-000051 | 21 | 2 / 2 | 100% |
| train-000014 | 22 | 9 / 9 | 100% |
| train-000147 | 22 | 3 / 3 | 100% |
| train-000153 | 24 | 1 / 1 | 100% |
| train-000062 | 45 | 7 / 7 | 100% |

## Options (deferred — your call)

- **A — soften + ctx:** loosen v2's drop/skip rules (emit best-available surface form instead of dropping) + `num_ctx` 4096→8192.
- **B — keep v2 strict:** report the short-doc under-extraction as a finding; fix only mechanics (`num_ctx`, **atomic fact writes** to kill the mid-write corruption).
- **C — hold:** revisit after discussing with Manuel.

Regardless of A/B/C, making fact writes atomic (write to a temp file + rename) is worth doing — it removes the mid-run "empty doc" artifact that triggered this.

