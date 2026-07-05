# KB deep-research playbook

You have access to a GraphRAG knowledge-base (KB) MCP server with these tools:
`list_knowledge_bases`, `get_kb_details`, `list_documents`, `get_document`,
`query_knowledge_base`, `search_graph`. Use this playbook when the user asks a
non-trivial question over indexed documents (technical specs, internal wiki, …).

## 1. Always discover first — never query blind
1. `list_knowledge_bases` — pick the KB.
2. `get_kb_details(kb_id)` — confirm readiness: is it indexed? which methods are
   available (`available_methods`)? `global`/`drift` need community reports; if
   missing, fall back to `local`.

## 2. Route by question shape
- Simple factual → `query_knowledge_base(method="local"|"basic")`.
- Theme / overview → `method="global"` (needs community reports).
- Hybrid → `method="drift"`.
- "How do X and Y relate?" → `search_graph(q="X", hop=2)` first; if Y appears in
  the neighborhood, query with that context.
- Multi-part question → decompose into sub-questions, call `query_knowledge_base`
  **in parallel** for each.

## 3. Deep-research main flow
1. `get_kb_details` — confirm ready.
2. Plan: split the question into sub-questions.
3. Fan out: call `query_knowledge_base` in parallel, one per sub-question.
4. For relationship claims, verify with `search_graph`.
5. For direct quotes or precise numbers, verify with `get_document`.
6. Synthesize: every claim must cite `(source.name, document)`.
7. Claims you cannot trace → say "KB 中未找到明确证据" (no fabrication).

## 4. Citation rules
- Every factual claim → `(source.name, document)`.
- Direct quotes → use `get_document` to fetch exact text.
- Uncertain → write "未找到明确证据", do not guess.

## 5. Failure modes
- KB not indexed → tell the user, do not query.
- Method needs community reports but they're missing → fall back to `local`,
  inform the user.
- All sources empty → say so plainly, do not fabricate.

## Examples
- "我们 wiki 里 A 服务和 B 服务怎么交互?" → `search_graph(q="A 服务", hop=2)`,
  check whether B is in the neighborhood → `query(method="local")` for detail →
  cite sources.
- "这版技术规格对延迟有什么要求?" → `list_documents` to find the spec →
  `get_document` to scan chunks → `query(method="local")` to locate → cite the
  exact chunk.
