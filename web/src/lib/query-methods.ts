/** The six retrieval methods — shared by query/chat surfaces (four GraphRAG + cypher/hybrid via Neo4j). */
export interface QueryMethod {
  key: string;
  name: string;
  desc: string;
  /** Methods that require community reports (must index with reports first). */
  needsReports: boolean;
}

export const QUERY_METHODS: QueryMethod[] = [
  { key: "local", name: "local", desc: "实体检索 + 社区摘要增强", needsReports: false },
  { key: "global", name: "global", desc: "全量社区报告 map-reduce", needsReports: true },
  { key: "drift", name: "drift", desc: "密集检索优先搜索", needsReports: true },
  { key: "basic", name: "basic", desc: "仅文本单元向量搜索（最快）", needsReports: false },
  { key: "cypher", name: "cypher", desc: "Text2Cypher：LLM 生成 Cypher 查询图", needsReports: false },
  { key: "hybrid", name: "hybrid", desc: "向量召回 + Cypher 多跳遍历", needsReports: false },
];
