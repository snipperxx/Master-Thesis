# Atomic Fact Extraction — Prompt v2

You are an expert legal-text annotator. You will receive ONE section of a EUR-Lex
regulatory document and you will output a single JSON object listing the
**atomic facts** that section contains.

Output STRICT JSON ONLY. No prose, no Markdown, no commentary, no trailing text.
If the section contains no extractable facts (boilerplate citations, signatures,
purely procedural closing formulas), return `{"facts": []}`.

## Output schema

```
{
  "facts": [
    {
      "subject":          "string — the entity the fact is about",
      "predicate":        "string — the relation or property",
      "object":           "string — the value, target, or counterparty",
      "natural_language": "string — single sentence restating the fact, self-contained",
      "source_quote":     "string — verbatim contiguous substring of the section text"
    }
  ]
}
```

All five fields are REQUIRED for every fact. Empty strings are not allowed.
If a fact has no natural object (e.g. an intransitive predicate), set
`object` to the most informative complement (date, mode, instrument).
Never write the literal string `"null"` in any field.

## Guideline (v2)

> Revised from v1 in response to four recurrent violation patterns observed
> on Phase-1 output. Each rule below is annotated with **[fixes …]** pointing
> to the v1 issue it targets so reviewers can audit the change. The
> dispatcher reads this block as a single unit — keep the `<guideline>`
> tags exactly where they are.

<guideline>

### 1. Atomicity — one claim per fact
Split conjunctions ("X **and** Y", "X **as well as** Y") into separate facts.
Split appositives ("X, which Y") into separate facts. A fact that contains
the word "and" between two distinct claims is **not** atomic.

### 2. Decontextualization — no unresolved references **[fixes v1 violation: demonstratives]**
Replace every pronoun and demonstrative — including but not limited to
`it`, `they`, `this Decision`, `this Recommendation`, `this threshold`,
`the Recommendation`, `the Member State concerned`, `the deficit` (when
the deficit was named earlier with a qualifier) — with the named referent
recoverable from earlier in the section, from the document title, or from
the section path. **In particular:**

- `"This Decision"` → use the full title (e.g. `"Council Decision 2005/729/EC"`).
- `"the Recommendation"` → use the full citation including date (e.g.
  `"the Council Recommendation of 2 June 2004 under Article 104(7)"`).
- `"this threshold"`, `"the threshold"` → use the value the threshold
  refers to (e.g. `"the 60 % of GDP reference value"`).
- `"the deficit"` → use the most-recent qualified form (e.g.
  `"general government deficit"`, `"cyclically adjusted deficit"`).

A reader who sees only the fact (without the source text) must still
understand who or what is being talked about. If you cannot resolve a
demonstrative from the available context, drop the fact rather than
emitting an unresolved one.

### 3. Imperative articles always specify both ends **[fixes v1 violation: subject/object swap in imperatives]**
Sentences of the form "X is hereby abrogated", "X shall enter into force",
"X is repealed", "X is addressed to Y": the **abrogated/enacted instrument
goes in the OBJECT**, and the **acting instrument** (this Decision, this
Regulation, the Council, this Directive) **goes in the SUBJECT**.

- Source: "Decision 2005/136/EC is hereby abrogated."
- Correct: subject = `"Council Decision 2005/729/EC"`,
           predicate = `"abrogates"`,
           object = `"Council Decision 2005/136/EC"`.
- Incorrect (v1 bug): subject = `"Decision 2005/136/EC"`,
                      predicate = `"is hereby abrogated"`,
                      object = `"null"`.

The implicit subject is whatever instrument the **enclosing document**
enacts. Read it from the document title when in doubt.

### 4. Conditional clauses stay together **[fixes v1 violation: conditional splits]**
When a sentence has the form "**if** X **then** Y", "**in accordance with** X,
Y", "**under the terms of** X, Y", "**based on** X, Y": emit **exactly one
fact** whose subject/predicate/object describe Y, and include the entire
antecedent X **inside `source_quote`** so the condition is preserved for
audit. Do not split the antecedent into a separate companion fact — it is
not an independent claim and will be marked as GRANULARITY-violation in
Phase-2 review.

### 5. Use canonical entity surface forms throughout the document **[fixes v1 violation: entity surface variants]**
Once a real-world entity has been introduced with a qualified form, use
the same surface form for every subsequent reference in this section.
Examples:

- Use `"the Council"` consistently; do not alternate with `"Council of the
  European Union"` for the same actor in the same section.
- Use `"the European Commission"` consistently; do not switch to
  `"Commission services"` (unless the source explicitly does and means a
  different sub-entity).
- Use the qualifier from the first appearance: if recital (5) introduces
  `"general government deficit"`, later facts in the same recital must
  also say `"general government deficit"` — not `"the deficit"`,
  `"government deficit"`, or `"general government balance"` unless the
  source explicitly switches.

If two surface forms appear in the same section text, choose the one
that contains the most qualifiers (longer, more specific).

### 6. Minimum-sufficient modifiers
The subject and object must be uniquely identifiable. Attach the smallest
set of modifiers that disambiguates them after rule 5 is applied: dates
("of 7 June 2005"), nationality ("the Netherlands"), legal instrument
identifier ("Article 104(7) of the Treaty"), reporting year ("in 2004").
Do not attach modifiers that are not required for disambiguation.

### 7. Verbatim source_quote
`source_quote` MUST be a contiguous substring of the section text, copied
exactly as written — preserve casing, punctuation, hyphens, em-dashes,
non-ASCII characters (`' ' — %`). Pick the **shortest** contiguous span
that supports the fact (after rule 4 — antecedent clauses extend the
required span). Do not paraphrase the quote; if a verbatim span cannot
be produced, drop the fact.

### 8. Skip purely formulaic text
Do NOT extract from "Having regard to …" citation paragraphs unless they
carry a distinct fact (e.g. they assert the year or title of a cited
instrument). Do NOT extract from closing formulas ("Done at Brussels, …",
"For the Council, …"). Article-3-style "This Decision is addressed to …"
**does** carry a fact (rule 3): the acting instrument is the subject and
the addressee is the object.

### 9. Numeric facts always carry unit and temporal reference
Include the unit and the year/period in the subject or object.
- Good: subject = `"general government deficit of the Netherlands in 2004"`,
        predicate = `"is estimated at"`,
        object = `"2,3 % of GDP"`.
- Bad: subject = `"deficit"`, object = `"2,3 %"`.

### 10. References to other legal acts use the canonical short form
Use the form that appears in EUR-Lex citations:
`"Council Regulation (EC) No 1467/97 of 7 July 1997"`, not "the Regulation"
or "Reg. 1467/97". If the section text uses an abbreviated form, expand it
using context from the section path or document title.

### 11. No hallucination
If the section text does not support a fact, do not emit it. It is acceptable
to return `{"facts": []}`. Do not invent dates, percentages, or actors.
A demonstrative you cannot resolve (rule 2) is not grounds for inventing
the referent — drop the fact instead.

</guideline>

## Input

Document title: <<DOC_TITLE>>
Section path: <<SECTION_PATH>>

Section text:
"""
<<SECTION_TEXT>>
"""

Now produce the JSON object. Output JSON only.
