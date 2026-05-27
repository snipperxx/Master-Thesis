<!--
Phase-2 Layer-2 arbitrator prompt.

Sentinel substitution (handled by src/conflict_layer2.py — see
extractor.render_prompt for the pattern):

    <<DOC_TITLE>>     full document title for context
    <<SOURCE_QUOTE>>  the literal source span both annotators saw
    <<FACT_A>>        natural-language form of annotator A's fact
    <<FACT_B>>        natural-language form of annotator B's fact

Important — keep the LABEL set fixed:
    CONTRADICTION  — A and B make incompatible claims about the same world fact
    GRANULARITY    — A is one fact, B is the same content split into N parts (or vice versa)
    REDUNDANCY     — A and B express the same claim with different surface form
    NO_CONFLICT    — A and B are about different things; not in conflict

Output contract is JSON only. Layer-1 has already filtered the cosine≥0.95+
high-overlap REDUNDANCY trivia, so most calls here will be CONTRADICTION
or GRANULARITY decisions — keep that in mind when reading the response.
-->

You are arbitrating two atomic facts extracted by two different annotators from the same legal text. Your job is exactly one classification.

The source span both annotators read:
"<<SOURCE_QUOTE>>"

From a document titled: <<DOC_TITLE>>

Annotator A produced:
"<<FACT_A>>"

Annotator B produced:
"<<FACT_B>>"

Classify the relation between A and B with exactly one of these four labels:

- CONTRADICTION — A and B describe the same situation but make incompatible claims (different numeric values, opposite polarity, mutually exclusive states).
- GRANULARITY — A and B describe the same situation at different decomposition levels (e.g. A merges what B splits into two facts, or vice versa).
- REDUNDANCY — A and B express the same claim in different surface forms (paraphrase, entity surface variant, voice flip) with no semantic divergence.
- NO_CONFLICT — A and B describe different aspects of the source text and are not in conflict.

Return strictly this JSON object, nothing else:

{"label": "CONTRADICTION|GRANULARITY|REDUNDANCY|NO_CONFLICT", "reason": "<= 25 words explaining the decision in plain English"}
