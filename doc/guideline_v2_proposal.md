# Guideline v2 (proposal) — leaner schema, first-class deontic + logic

> 草案。放在 `doc/`,不会被 UI 的 `prompts/extract_*.md` 扫描当成活跃版本。
> 评审 OK 后复制成 `prompts/extract_v2.md` 即可启用。

## 相对 v1 (= 原 v5) 的改动

**理论增益**(补上分析里的四个缺口):

- **Deontic 模态一等公民** — 新增 `modality` ∈ {assertion, obligation, permission, prohibition};`shall/may/must` 不再塞进谓词。这是法律规范最核心的逻辑维度。
- **否定一等公民** — 新增 `polarity` ∈ {positive, negative};不再靠 KG 端 `_NEG_RE` 正则去猜,也能区分内容否定与道义禁止。
- **蕴含一等公民** — `logic_op` 增加 `IF` + `logic_role` ∈ {antecedent, consequent}:条件变成 statement↔statement 的结构,取代易丢信息、只能挂一个实体的自由文本 `condition`。
- **统一逻辑机制** — 把"同句共断"(旧 `group_id`)并入 `logic_op:AND`;一个 `logic_group` 同时承载 AND / OR / XOR / IF。

**更简洁** — 13 条规则压成 6 条;每个 fact = 一个 reified statement(去掉嵌套 `triples[]` 数组,与存储层 `AtomicFact` 的"一三元组一事实"对齐)。

**刻意保留的限制** — 不支持嵌套布尔树(`(A∧B)∨C`)。单步噪声抽取下硬标嵌套得不偿失,违背"噪声单步即信号"的设计;遇到嵌套取主算子、内层写进 `natural_language`。

## 采用前需要的 schema / pipeline 改动

- `src/schema.py` `AtomicFact`:加 `modality: str = ""`、`polarity: str = "positive"`、`logic_role: str = ""`;`condition` 保留作可选 gloss 或弃用。
- `src/kg_build.py` `build_reified_graph`:statement 节点直接读 `modality`/`polarity`(替换 `_NEG_RE`/`_MODAL_RE` 启发式);`logic_op == "IF"` 时在 antecedent → consequent 之间画 statement→statement 的 "if" 边(而非现在的 statement→entity)。
- `src/extractor.py`:透传新字段(否则只会落进 `extra`)。
- `src/conflict_layer1.py`:极性比较改用结构化 `polarity` 而非 `polarity_markers` 正则。

---

## The prompt (copy this into `prompts/extract_v2.md`)

# Atomic Fact Extraction — v2

You are an expert legal-text annotator. You receive ONE section of a EUR-Lex
regulatory act and output a single JSON object listing its **atomic facts**.

Output STRICT JSON ONLY — no prose, no Markdown. Return `{"facts": []}` only for a
pure citation block, a signature/closing formula, or a bare header. Almost
everything else yields facts.

## Output schema

```
{
  "facts": [
    {
      "natural_language": "one self-contained sentence restating this statement",
      "source_quote":     "verbatim contiguous substring of the section text",
      "subject":   "string",
      "predicate": "the relation, WITHOUT any deontic verb (no shall/may/must)",
      "object":    "string",
      "modality":  "assertion | obligation | permission | prohibition",
      "polarity":  "positive | negative",
      "temporal":  "ordering/deadline phrase (after/before/within N days), or \"\"",
      "logic_group": "shared id tying facts of ONE logical construction, or \"\"",
      "logic_op":    "AND | OR | XOR | IF | \"\"",
      "logic_role":  "antecedent | consequent  (only when logic_op = IF; else \"\")"
    }
  ]
}
```

`natural_language`, `source_quote`, `subject`, `predicate`, `object`, `modality`,
and `polarity` are REQUIRED and non-empty. Never write the literal `"null"`.

## Principles

**1. Atomicity — one relation per fact.** Exactly one subject–predicate–object per
fact. Split "X and Y", appositions, and each coordinate branch into separate
facts. A relative / scope / exception / cross-reference clause is its OWN fact,
never buried inside an object.

**2. Decontextualize.** Replace pronouns and definite references ("it", "this
Regulation", "the threshold", "the deficit") with the named referent from the
section, title, or section path. Use ONE canonical surface form per entity (the
longest / most-qualified one); cite other acts in canonical short form ("Council
Regulation (EEC) No 1418/76"). If a reference is truly unresolvable, still emit
the fact.

**3. Deontic force → `modality`; keep it OUT of the predicate.**
- `obligation` — shall / must / is required to
- `permission` — may / is entitled to / it is for X to
- `prohibition` — shall not / may not / is forbidden to
- `assertion` — a statement of fact (recitals, findings, definitions, "X is Y")

Put the bare act in the predicate ("fix", "decide", "apply"), not "shall fix".

**4. Negation → `polarity`.** `negative` when the propositional content is negated
(not / no / never / without / no longer / fails to / ceases to); otherwise
`positive`. A `prohibition` already forbids its content — keep its `polarity`
`positive` unless the forbidden act is itself negated ("may refrain from acting"
= permission + negative).

**5. Logic — one construction shares a `logic_group` + `logic_op`.** Emit one fact
per branch; never pack "A or B" into a single object.
- `AND` — both / all hold (also for several relations co-asserted by one sentence)
- `OR` — inclusive (one or more)
- `XOR` — exclusive (exactly one)
- `IF` — implication: the antecedent clause is a fact with `logic_role:"antecedent"`;
  each consequent is a fact with `logic_role:"consequent"`; all share the `logic_group`.

Do NOT nest groups. If a construction nests ("(A and B) or C"), pick the dominant
operator for the group and carry the inner relation in each branch's
`natural_language`.

**6. Coverage + fidelity.** Work clause by clause, left to right; cover every
clause — main, subordinate, relative, conditional (BOTH antecedent and
consequent), exception, qualifier, cross-reference, amount, deadline, addressee.
`source_quote` is copied verbatim (casing, punctuation, non-ASCII). Capture only
what the text STATES; never fabricate. Skip ONLY "Having regard to …" citations,
closing formulas, and bare headers — a "Whereas …" recital IS covered.

## Worked examples  (one line per emitted fact: subject | predicate | object | modality | polarity | logic_group | logic_op | logic_role | temporal)

"the Commission shall decide either to fix a maximum export refund or to take no action"
- the Commission | fix | a maximum export refund | permission | positive | g1 | XOR | | |
- the Commission | act | on the tenders        | permission | negative | g1 | XOR | | |

"If the general government deficit exceeds 3 % of GDP, the Council shall adopt a decision"
- the general government deficit | exceeds | 3 % of GDP | assertion | positive | g2 | IF | antecedent | |
- the Council | adopt | a decision | obligation | positive | g2 | IF | consequent | |

"Within 30 days of notification the Member State shall inform the Commission"
- the Member State | inform | the Commission | obligation | positive | | | | within 30 days of notification

"Regulation (EEC) No 1418/76 shall no longer apply to imports of rice"
- Regulation (EEC) No 1418/76 | apply | to imports of rice | assertion | negative | | | | |

## Input

Document title: <<DOC_TITLE>>
Section path: <<SECTION_PATH>>

Section text:
"""
<<SECTION_TEXT>>
"""

Produce the JSON object. Cover every clause. Output JSON only.
