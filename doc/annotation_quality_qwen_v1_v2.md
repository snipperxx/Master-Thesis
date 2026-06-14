# Annotation Quality: qwen3.5:4b under Guideline v1 vs v2

**Doc:** train-000000 (Council Decision 2005/729/EC) · **Model:** qwen3.5:4b
(Q4_K_M, Ollama) · **Runs:** v1 = 2026-05-22 baseline (32 facts), v2 =
2026-06-10 closed-loop revision (25 facts). All 57 facts read and checked
against the source text; counts verified programmatically.
Companion to `ui_iteration_log.md` §2.20/§2.25.

## 1. Scorecard

| Dimension (guideline rule) | v1 (32 facts) | v2 (25 facts) | Verdict |
|---|---|---|---|
| `source_quote` verbatim in source (§4) | 32/32 | 25/25 | both perfect |
| Numeric fidelity vs source | 12/12 correct | 10/10 correct | model copies numbers reliably |
| Object = literal `"null"` (pattern #2) | 1 | 0 | **v2 fixed** |
| Same-S+P split groups in one section (pattern #3, §6) | 1 group ×3 | 0 | **v2 fixed** |
| Preposition/adverbial-led objects (P/O boundary cut) | 11/32 (34%) | 3/25 (12%) | **v2 much better** |
| Unresolved demonstratives (pattern #1, §2) | 2 | 2 | not eradicated |
| Numeric facts carrying a year (§7) | 11/12 (92%) | **6/10 (60%)** | **v2 regression** |
| Semantically required preposition dropped | 0 | 2 | **v2 regression (new)** |
| Entity confusion errors | 1 | 0 | v2 fixed |
| Sections covered | 8 (recitals[5] = 0) | 9 (incl. recitals[5]) | v2 better |

## 2. What v2 visibly fixed (examples)

- **Abrogation direction + self-reference (pattern #2).**
  v1[30]: `(Decision 2005/136/EC, is hereby abrogated, "null")`.
  v2[23]: `(Council Decision 2005/729/EC, abrogates, Council Decision
  2005/136/EC)` — the deciding act is resolved *from the document title*,
  exactly what §2-v2 demanded. Flagship win.
- **Modifier-split (pattern #3).** v1 split Article 1 into 3 facts sharing
  `(correction…, was completed, ·)` with adverbial objects ("in 2004" /
  "under the terms of…" / "in accordance with…"). v2 emits one core fact;
  the adverbials stay in `natural_language`/quote.
- **P/O boundary discipline.** Adverbial-led objects dropped 34% → 12%
  ("in the form of substantial savings measures" as an O is gone).
- **Entity confusion.** v1[16] attributed the 2,0 %-decline to the
  *government balance* (conflating two adjacent sentences); v2[12] binds it
  to the *deficit*, matching the source.

## 3. What v2 newly broke (the over-correction)

v2's pressure toward minimal, entity-like objects (obj tokens 7.3 → 6.0)
removed load-bearing material, not just noise:

- **Temporal anchors dropped (§7 regression).** v1: "decline … *to 2,0 %
  of GDP* (P carries 'in 2005')", "1,6 % of GDP *in 2006*", "fell to 1,2 %
  *in 2004*". v2 emits `projected to decline to | 2,0 % of GDP` with **no
  year anywhere in S/P/O** (4 of 10 numeric facts). Two projections for
  different years ("2,0 %" for 2005, "1,6 %" for 2006) become temporally
  indistinguishable triples — *worse* for downstream contradiction
  detection, since Layer-1's antonym trap now sees two bare percentages on
  near-identical sentences.
- **Semantically required prepositions dropped.** v2[04] `(definitions…,
  are laid down, the Protocol…)` — source: "laid down **in** the Protocol";
  v2[05] `(data…, are provided, the Commission)` — source: "provided **by**
  the Commission". The bare-NP object reads as a direct object and inverts
  the role (locative → patient, agent → patient). v1 kept the prepositions
  and was unambiguous.
- **Demonstratives not eradicated.** v2[02] `This Recommendation`, v2[19]
  `this threshold` (same residue as v1[26]) — the rule works for the
  document's self-reference but not yet for intra-recital anaphora.

## 4. Reading

1. **The closed loop works as designed:** every v2 target pattern moved in
   the intended direction, and the regression is itself *rule-shaped* —
   fixable by wording, not by model swap. Candidate v3 rules:
   - §7': "Projections/estimates MUST carry their reference year inside
     subject or object, even when the year is implicit from context."
   - §3': "Keep a preposition with the object when it marks agent (*by*),
     location (*in/under*), or instrument — bare-NP objects only for
     direct patients."
   - §2': add an example resolving intra-sentence anaphora ("this
     threshold" → "the 60 % of GDP reference value").
2. **Model vs guideline attribution sharpened:** qwen's quote anchoring
   and numeric copying are flawless across both versions; its failure
   modes are *boundary decisions* (where P ends and O starts, which
   qualifier survives) — precisely the dimensions guidelines steer. The
   earlier gemma analysis (§2.20: 43% verbatim, cross-sentence number
   binding) shows the opposite profile: errors guidelines *cannot* fix.
   The tool now distinguishes these two failure classes empirically.
3. **Schema limitation confirmed (user's hypothesis):** 34% adverbial
   objects in v1 is direct evidence that a bare (S,P,O) triple forces
   arbitrary cuts when legal prose attaches time/condition/agent to every
   clause. v2 reduced the symptom by *deleting* qualifiers — trading §7
   compliance for node simplicity. The structural fix would be an n-ary
   schema (optional `qualifiers: {time, condition, agent, location}`)
   rendered as edge badges, keeping nodes bare AND facts complete. This
   changes the annotation-unit definition → requires supervisor sign-off;
   measurable with the existing v1→v2→v3 loop machinery.

