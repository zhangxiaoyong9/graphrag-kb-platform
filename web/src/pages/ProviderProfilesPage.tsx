import { useEffect, useState } from "react";
import { Badge, Button, Card, CardHeader, Field } from "../components/ui";
import { IconPlus, IconTrash, IconKey } from "../components/icons";
import { listProfiles, createProfile, deleteProfile } from "../api/client";
import type { ProviderProfile } from "../api/types";

type Kind = "llm" | "embedding";

/** Provider 配置: manage reusable LLM/embedding connection profiles (keys encrypted at rest). */
export function ProviderProfilesPage() {
  const [profiles, setProfiles] = useState<ProviderProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<Kind>("llm");

  // create-form state
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [apiBase, setApiBase] = useState("");
  const [apiVersion, setApiVersion] = useState("");
  const [structured, setStructured] = useState(true);
  const [keys, setKeys] = useState<string[]>([""]);
  const [busy, setBusy] = useState(false);

  const reload = () => {
    setLoading(true);
    listProfiles(kind)
      .then(setProfiles)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  };
  useEffect(reload, [kind]);

  const submit = async () => {
    if (!name || !provider || !model) return;
    setBusy(true);
    setError(null);
    try {
      await createProfile({
        name, kind, provider, model,
        api_base: apiBase || null,
        api_version: apiVersion || null,
        api_keys: keys.filter((k) => k),
        structured_output: structured,
      });
      setName(""); setProvider(""); setModel(""); setApiBase(""); setApiVersion(""); setKeys([""]);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    setError(null);
    try {
      await deleteProfile(id);
      reload();
    } catch (e) {
      setError(`删除失败：${(e as Error).message}（可能被知识库引用）`);
    }
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Provider 配置"
          subtitle="命名的 LLM / Embedding 连接配置，密钥加密入库；新建知识库时下拉选用"
          icon={<IconKey width={18} height={18} />}
          actions={
            <div className="flex gap-1">
              {(["llm", "embedding"] as Kind[]).map((k) => (
                <Button key={k} size="sm" variant={kind === k ? "primary" : "secondary"} onClick={() => setKind(k)}>
                  {k === "llm" ? "LLM" : "Embedding"}
                </Button>
              ))}
            </div>
          }
        />

        {loading ? (
          <p className="mt-4 text-[13px] text-muted">加载中…</p>
        ) : error && !profiles.length ? (
          <p className="mt-4 text-[13px] text-danger">{error}</p>
        ) : profiles.length === 0 ? (
          <p className="mt-4 rounded-xl border border-dashed border-line-strong px-3 py-6 text-center text-[13px] text-muted">
            还没有 {kind} profile，用下方表单新建一个。
          </p>
        ) : (
          <ul className="mt-4 divide-y divide-line rounded-xl border border-line">
            {profiles.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink">{p.name}</p>
                  <p className="mt-0.5 text-xs text-muted nums">
                    {p.provider} · {p.model}
                    {p.api_base ? ` · ${p.api_base}` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge tone="info">{p.api_keys_count} key</Badge>
                  {p.kind === "llm" && (
                    <Badge tone={p.structured_output ? "success" : "neutral"}>
                      {p.structured_output ? "json_schema" : "plain"}
                    </Badge>
                  )}
                  <Button size="sm" variant="danger" aria-label={`删除 ${p.name}`} onClick={() => remove(p.id)}>
                    <IconTrash width={14} height={14} /> 删除
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader title="新建 profile" subtitle="密钥仅写入、列表只回显数量" icon={<IconPlus width={18} height={18} />} />
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <Field label="名称"><input className="input" placeholder="名称，如 DeepSeek" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="provider" hint="deepseek / openai / ollama / azure"><input className="input" placeholder="provider" value={provider} onChange={(e) => setProvider(e.target.value)} /></Field>
          <Field label="model"><input className="input" placeholder="deepseek-chat" value={model} onChange={(e) => setModel(e.target.value)} /></Field>
          <Field label="api_base（可选）"><input className="input" placeholder="https://api.deepseek.com" value={apiBase} onChange={(e) => setApiBase(e.target.value)} /></Field>
          {provider.toLowerCase() === "azure" && (
            <Field label="api_version（Azure）"><input className="input" placeholder="2024-06-01" value={apiVersion} onChange={(e) => setApiVersion(e.target.value)} /></Field>
          )}
        </div>

        {kind === "llm" && (
          <label className="mt-3 flex items-center gap-2 text-[13px] text-body">
            <input type="checkbox" checked={structured} onChange={(e) => setStructured(e.target.checked)} />
            structured_output（json_schema；DeepSeek 等不支持请取消勾选）
          </label>
        )}

        <div className="mt-4">
          <p className="mb-2 text-[13px] font-medium text-body">API Keys</p>
          <div className="space-y-2">
            {keys.map((k, i) => (
              <div key={i} className="flex gap-2">
                <input
                  type="password"
                  className="input"
                  placeholder="sk-..."
                  value={k}
                  onChange={(e) => setKeys(keys.map((v, j) => (j === i ? e.target.value : v)))}
                />
                <Button size="sm" variant="ghost" aria-label="移除该 key" onClick={() => setKeys(keys.filter((_, j) => j !== i))}>
                  <IconTrash width={14} height={14} />
                </Button>
              </div>
            ))}
          </div>
          <Button size="sm" variant="secondary" className="mt-2" onClick={() => setKeys([...keys, ""])}>
            <IconPlus width={14} height={14} /> 新增 key
          </Button>
        </div>

        {error && <p className="mt-3 text-[13px] text-danger">{error}</p>}
        <Button variant="primary" className="mt-4" disabled={busy || !name || !provider || !model} onClick={submit}>
          {busy ? "保存中…" : "保存"}
        </Button>
      </Card>
    </div>
  );
}

export default ProviderProfilesPage;
