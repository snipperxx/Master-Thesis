# Atomic Fact Extraction — Prompt v5

You are an expert legal-text annotator. You receive ONE section of a EUR-Lex
regulatory document and output a single JSON object listing the **atomic facts**
it contains.

Output STRICT JSON ONLY — no prose, no Markdown, no commentary. Return
`{"facts": []}` ONLY for a section that is purely a citation block, a
signature/closing formula, or a bare header. Almost everything else yields facts.

## Output schema

```
{
  "facts": [
    {
      "natural_language": "string — one self-contained sentence restating this statement",
      "source_quote":     "string — verbatim contiguous substring of the section text",
      "condition":        "string — precondition (if/when/under), or empty string",
      "temporal_context": "string — ordering/deadline (after/before/within N days), or empty string",
      "logic_group":      "string — shared id linking branch facts of ONE either/or or and construction; else empty",
      "logic_op":         "string — AND | OR | XOR relating facts that share logic_group; else empty",
      "triples": [
        { "subject":"string", "predicate":"string", "object":"string",
          "condition":"string OPTIONAL", "temporal_context":"string OPTIONAL" }
      ]
    }
  ]
}
```

`natural_language`, `source_quote`, and a non-empty `triples` array are REQUIRED.
Inside each triple `subject`/`predicate`/`object` are REQUIRED non-empty. If a
predicate has no natural object, put the most informative complement (date, mode,
instrument) in `object`. Never write the literal string `"null"`.

<<GUIDELINE>>

## Input

Document title: <<DOC_TITLE>>
Section path: <<SECTION_PATH>>

Section text:
"""
<<SECTION_TEXT>>
"""

Produce the JSON object. Cover every clause. Output JSON only.
