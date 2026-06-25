import { Card, CardHeader, Badge } from "../components/ui";
import { Link } from "react-router-dom";
import {
  IconGear,
  IconSparkle,
  IconCpu,
  IconLayers,
  IconUpload,
  IconSearch,
  IconKey,
} from "../components/icons";

interface ConfigSection {
  icon: React.ReactNode;
  title: string;
  rows: { k: string; v: string }[];
  note?: string;
}

const SECTIONS: ConfigSection[] = [
  {
    icon: <IconSparkle width={18} height={18} />,
    title: "LLM 模型",
    rows: [
      { k: "配置方式", v: "按知识库 settings_yaml 透传给 graphrag（litellm），默认 OpenAI / Azure" },
      { k: "示例", v: `{"llm":{"model_provider":"deepseek","model":"deepseek-chat"}}` },
      { k: "凭证解析顺序", v: "llm.api_key_env → {PROVIDER}_API_KEY → 显式 api_key（密钥绝不入库）" },
    ],
    note: "在「知识库管理」创建 KB 时填写 settings_yaml。",
  },
  {
    icon: <IconCpu width={18} height={18} />,
    title: "Embedding",
    rows: [
      { k: "配置方式", v: "同样走 KB settings_yaml（如 {\"embedding\":{\"model\":\"...\"}}）" },
      { k: "向量存储", v: "LanceDB（本地），落在 <data_root>/vectors/" },
    ],
  },
  {
    icon: <IconLayers width={18} height={18} />,
    title: "索引",
    rows: [
      { k: "索引方法", v: "standard（LLM 抽取）/ fast（NLP 抽取 + LLM 摘要）" },
      { k: "任务类型", v: "full（全量重建）/ incremental（仅处理新增/变更文档）" },
      { k: "流水线", v: "chunk → extract_graph → summarize → finalize → communities → community_reports → embeddings" },
    ],
  },
  {
    icon: <IconUpload width={18} height={18} />,
    title: "上传限制",
    rows: [
      { k: "单文件上限", v: "25 MiB（环境变量 KB_MAX_UPLOAD_BYTES 可调）" },
      { k: "支持格式", v: ".txt / .md / .pdf / .docx / .html 等（经 markitdown 解析）" },
      { k: "删除", v: "删除文档不会回缩图谱——需重跑增量任务刷新索引" },
    ],
  },
  {
    icon: <IconSearch width={18} height={18} />,
    title: "查询方式",
    rows: [
      { k: "local", v: "实体检索 + 社区摘要增强（无需社区报告）" },
      { k: "global", v: "全量社区报告 map-reduce（需社区报告）" },
      { k: "drift", v: "密集检索优先搜索（需社区报告）" },
      { k: "basic", v: "仅文本单元向量搜索（最快，无需社区报告）" },
    ],
    note: "global / drift 依赖社区报告；DeepSeek 需在 KB 设置中开启 community_reports.structured_output: false。",
  },
];

/** Read-only configuration overview. No write API — guidance only, never fakes a save. */
export default function SettingsPage() {
  return (
    <div className="space-y-5">
      <div className="card flex items-start gap-3 border-info/30 bg-info-soft/40 p-4">
        <IconGear width={20} height={20} className="mt-0.5 shrink-0 text-info" />
        <div className="text-[13px] text-body">
          <p className="font-medium text-ink">只读说明</p>
          <p className="mt-1">
            本页展示平台的配置模型。当前版本<strong>未提供在线写入接口</strong>——模型 / Embedding /
            索引等配置在创建知识库时通过 <code className="rounded bg-surface px-1 font-mono text-[12px]">settings_yaml</code> 设定，
            运行参数（凭证、上传上限等）通过环境变量设定。本页不会保存任何更改。
          </p>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        {SECTIONS.map((s) => (
          <Card key={s.title}>
            <CardHeader title={s.title} icon={s.icon} />
            <div className="mt-4 space-y-2.5">
              {s.rows.map((r) => (
                <div key={r.k} className="flex flex-col gap-0.5 border-b border-line pb-2 last:border-0 last:pb-0">
                  <span className="text-[12px] font-medium text-muted">{r.k}</span>
                  <span className="font-mono text-[12px] leading-relaxed text-ink">{r.v}</span>
                </div>
              ))}
              {s.note && <p className="pt-1 text-[12px] text-muted">· {s.note}</p>}
            </div>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader
          title="API Key 管理"
          subtitle="能力预留"
          icon={<IconKey width={18} height={18} />}
          actions={<Badge tone="neutral">未启用</Badge>}
        />
        <div className="mt-4">
          <p className="text-[13px] text-muted">
            当前版本未启用 API Key 管理。如需程序化访问，请直接调用 REST 接口（见「系统状态 · API 接口」）。
            访问凭证 / 限流 / 计费属于后续 SaaS 化阶段，不在本版本范围。
          </p>
          <p className="mt-2 inline-flex items-center gap-1.5 text-[12px] text-muted">
            <IconKey width={14} height={14} /> 前往
            <Link to="/api-keys" className="text-brand hover:underline">API Keys</Link>
            查看能力预留说明。
          </p>
        </div>
      </Card>
    </div>
  );
}
