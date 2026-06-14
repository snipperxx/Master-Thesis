# Atomic Fact Extraction — Prompt v1

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
      "condition":        "string — logical precondition (if/when/under clause), or empty string if unconditional",
      "temporal_context": "string — ordering/deadline (after/before/within N days, subsequently), or empty string if none",
      "natural_language": "string — single sentence restating the fact, self-contained",
      "source_quote":     "string — verbatim contiguous substring of the section text"
    }
  ]
}
```

subject, predicate, object, natural_language and source_quote are REQUIRED (non-empty). `condition` and `temporal_context` are optional — use an empty string when not applicable.

## Guideline (v1)

> This block is the editable rule set. Phase-4 will revise it to `v2`; do not
> change wording here without bumping `guideline_version` in the dispatcher.

<guideline>

### 1. Atomicity — one claim per fact
Split conjunctions ("X **and** Y", "X **as well as** Y") into separate facts.
Split appositives ("X, which Y") into separate facts. A fact that contains
the word "and" between two distinct claims is **not** atomic.

### 2. Decontextualization — no unresolved references
Replace every pronoun and demonstrative ("it", "they", "this Decision",
"the Recommendation", "the Member State concerned") with the named referent
recoverable from earlier in the section or from the section path. A reader
who sees only the fact (without the source text) must still understand who or
what is being talked about.

### 3. Minimum-sufficient modifiers
The subject and object must be uniquely identifiable. Attach the smallest
set of modifiers that disambiguates them: dates ("of 7 June 2005"),
nationality ("the Netherlands"), legal instrument identifier
("Article 104(7) of the Treaty"), reporting year ("in 2004"). Do not
attach modifiers that are not required for disambiguation.

### 4. Verbatim source_quote
`source_quote` MUST be a contiguous substring of the section text, copied
exactly as written — preserve casing, punctuation, hyphens, em-dashes,
non-ASCII characters (`‘ ’ — %`). Pick the **shortest** contiguous span
that supports the fact. Do not paraphrase the quote; if a verbatim span
cannot be produced, drop the fact.

### 5. Skip purely formulaic text
Do NOT extract from "Having regard to …" citation paragraphs unless they
carry a distinct fact (e.g. they assert the year or title of a cited
instrument). Do NOT extract from closing formulas ("Done at Brussels, …",
"For the Council, …", "This Decision is addressed to …" when it merely
restates the addressee already named in the title).

### 6. Conditions and ordering go in their own fields
If a fact holds only under a precondition ("**if** X", "**when** X", "**provided
that** X", "**under** X", "**in accordance with** X"): emit **one** fact whose
subject/predicate/object describe the consequent Y, and put the precondition
text X in the `condition` field. If the fact carries an order or deadline
("**after** X", "**before** X", "**within** 30 days", "**subsequently**"): put
that phrase in the `temporal_context` field. Keep the whole sentence in
`source_quote` for audit, and do NOT split the antecedent or ordering into a
separate fact. Leave a field as "" when it does not apply.

### 7. Numeric facts always carry unit and temporal reference
Include the unit and the year/period in the subject or object.
- Good: subject = `"general government deficit of the Netherlands in 2004"`,
        predicate = `"is estimated at"`,
        object = `"2,3 % of GDP"`.
- Bad: subject = `"deficit"`, object = `"2,3 %"`.

### 8. References to other legal acts use the canonical short form
Use the form that appears in EUR-Lex citations:
`"Council Regulation (EC) No 1467/97 of 7 July 1997"`, not "the Regulation"
or "Reg. 1467/97". If the section text uses an abbreviated form, expand it
using context from the section path.

### 9. No hallucination
If the section text does not support a fact, do not emit it. It is acceptable
to return `{"facts": []}`. Do not invent dates, percentages, or actors.

</guideline>

## Input

Document title: <<DOC_TITLE>>
Section path: <<SECTION_PATH>>

Section text:
"""
<<SECTION_TEXT>>
"""

Now produce the JSON object. Output JSON only.
