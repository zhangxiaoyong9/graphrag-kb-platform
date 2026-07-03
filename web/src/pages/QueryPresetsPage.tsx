import { useEffect, useState } from "react";
import {
  listQueryPresets,
  createQueryPreset,
  updateQueryPreset,
  deleteQueryPreset,
} from "../api/client";
import type { QueryPreset } from "../api/types";
import { Card, CardHeader, Button, Spinner, EmptyState } from "../components/ui";
import { IconSearch, IconPlus, IconTrash } from "../components/icons";

type Draft = Omit<QueryPreset, "id" | "is_builtin">;
const BLANK: Draft = {
  name: "",
  description: "",
  method: "local",
  community_level: null,
  response_type: null,
  top_k: null,
  temperature: null,
  system_prompt: null,
  hops: null,
  cypher_timeout_ms: null,
};

const toDraft = (p: QueryPreset): Draft => ({
  name: p.name,
  description: p.description,
  method: p.method,
  community_level: p.community_level,
  response_type: p.response_type,
  top_k: p.top_k,
  temperature: p.temperature,
  system_prompt: p.system_prompt,
  hops: p.hops,
  cypher_timeout_ms: p.cypher_timeout_ms,
});

/** 检索预设:全局跨 KB 的查询配置库;内置只读,自定义可新建/编辑/删除。 */
export default function QueryPresetsPage() {
  const [items, setItems] = useState<QueryPreset[] | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = async () => {
    try {
      setItems(await listQueryPresets());
    } catch {
      setItems([]);
      setError("加载预设列表失败");
    }
  };
  useEffect(() => {
    reload();
  }, []);

  const submit = async () => {
    if (!draft.name.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (editingId == null) {
        await createQueryPreset(draft);
      } else {
        await updateQueryPreset(editingId, draft);
      }
      setDraft(BLANK);
      setEditingId(null);
      await reload();
    } catch (e) {
      setError(`保存失败:${(e as Error).message ?? e}`);
    } finally {
      setBusy(false);
    }
  };

  const edit = (p: QueryPreset) => {
    setDraft(toDraft(p));
    setEditingId(p.id);
    setError(null);
  };

  const cancel = () => {
    setDraft(BLANK);
    setEditingId(null);
  };

  const remove = async (id: number) => {
    setError(null);
    try {
      await deleteQueryPreset(id);
      if (id === editingId) cancel();
      await reload();
    } catch (e) {
      setError(`删除失败:${(e as Error).message ?? e}`);
    }
  };

  if (items === null) return <Spinner />;

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="检索预设"
          subtitle="查询配置库 · 内置只读 · 跨知识库复用"
          icon={<IconSearch width={18} height={18} />}
        />
        <div className="mt-4 overflow-x-auto">
          {items.length === 0 ? (
            <EmptyState
              icon={<IconSearch />}
              title="还没有预设"
              hint="在下方新建,或在检索页「另存为预设」。"
            />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-[12px] text-muted">
                <tr>
                  <th className="py-2">名称</th>
                  <th>method</th>
                  <th>community_level</th>
                  <th>response_type</th>
                  <th>top_k</th>
                  <th>temperature</th>
                  <th>方法旋钮</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => (
                  <tr key={p.id} className="border-t border-line">
                    <td className="py-2 font-medium text-ink">
                      {p.name}
                      {p.is_builtin && <span className="ml-1 text-[10px] text-muted">内置</span>}
                      {p.description && (
                        <span className="ml-2 text-[11px] text-muted">{p.description}</span>
                      )}
                    </td>
                    <td className="font-mono text-[12px]">{p.method}</td>
                    <td>{p.community_level ?? "—"}</td>
                    <td>{p.response_type ?? "—"}</td>
                    <td>{p.top_k ?? "—"}</td>
                    <td>{p.temperature ?? "—"}</td>
                    <td className="font-mono text-[12px] text-muted">
                      {p.method === "hybrid" && p.hops != null
                        ? `hops=${p.hops}`
                        : p.method === "cypher" && p.cypher_timeout_ms != null
                        ? `timeout=${p.cypher_timeout_ms}ms`
                        : "—"}
                    </td>
                    <td className="space-x-2 whitespace-nowrap">
                      {!p.is_builtin && (
                        <>
                          <button
                            type="button"
                            className="text-[12px] text-brand hover:underline"
                            aria-label={`编辑 ${p.name}`}
                            onClick={() => edit(p)}
                          >
                            编辑
                          </button>
                          <button
                            type="button"
                            className="text-muted hover:text-danger"
                            title="删除"
                            aria-label={`删除 ${p.name}`}
                            onClick={() => remove(p.id)}
                          >
                            <IconTrash width={14} height={14} />
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {error && (
          <p className="mt-3 text-[13px] text-danger" role="alert">
            {error}
          </p>
        )}
      </Card>

      <Card>
        <CardHeader
          title={editingId == null ? "新建预设" : "编辑预设"}
          icon={<IconPlus width={18} height={18} />}
        />
        <div className="mt-4 space-y-3">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
            <input
              className="input"
              placeholder="名称"
              value={draft.name}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            />
            <select
              className="select"
              value={draft.method}
              onChange={(e) => setDraft({ ...draft, method: e.target.value })}
            >
              <option value="local">local</option>
              <option value="global">global</option>
              <option value="drift">drift</option>
              <option value="basic">basic</option>
              <option value="hybrid">hybrid</option>
              <option value="cypher">cypher</option>
            </select>
            <input
              className="input"
              placeholder="描述(可空)"
              value={draft.description}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
            />
            <input
              className="input"
              type="number"
              placeholder="community_level(可空)"
              value={draft.community_level ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, community_level: e.target.value ? Number(e.target.value) : null })
              }
            />
            <select
              className="select"
              value={draft.response_type ?? ""}
              onChange={(e) => setDraft({ ...draft, response_type: e.target.value || null })}
            >
              <option value="">response_type:默认</option>
              <option value="multiple paragraphs">多段</option>
              <option value="single paragraph">单段</option>
              <option value="bullet points">要点</option>
            </select>
            <input
              className="input"
              type="number"
              placeholder="top_k(可空)"
              value={draft.top_k ?? ""}
              onChange={(e) => setDraft({ ...draft, top_k: e.target.value ? Number(e.target.value) : null })}
            />
            <input
              className="input"
              type="number"
              step="0.05"
              placeholder="temperature(可空)"
              value={draft.temperature ?? ""}
              onChange={(e) =>
                setDraft({ ...draft, temperature: e.target.value ? Number(e.target.value) : null })
              }
            />
            {draft.method === "hybrid" && (
              <input
                className="input"
                type="number"
                min={1}
                max={5}
                placeholder="hops(可空,hybrid)"
                value={draft.hops ?? ""}
                onChange={(e) =>
                  setDraft({ ...draft, hops: e.target.value ? Number(e.target.value) : null })
                }
              />
            )}
            {draft.method === "cypher" && (
              <input
                className="input"
                type="number"
                min={1000}
                placeholder="cypher_timeout_ms(可空,cypher)"
                value={draft.cypher_timeout_ms ?? ""}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    cypher_timeout_ms: e.target.value ? Number(e.target.value) : null,
                  })
                }
              />
            )}
          </div>
          <textarea
            className="textarea h-20 font-mono text-[12px]"
            placeholder="system_prompt(覆盖该 method 的主回答 prompt;可空)"
            value={draft.system_prompt ?? ""}
            onChange={(e) => setDraft({ ...draft, system_prompt: e.target.value || null })}
          />
          <div className="flex items-center gap-2">
            <Button variant="primary" disabled={busy || !draft.name.trim()} onClick={submit}>
              {busy ? <Spinner /> : <IconPlus width={16} height={16} />}
              {editingId == null ? "新建" : "保存修改"}
            </Button>
            {editingId != null && (
              <Button variant="ghost" disabled={busy} onClick={cancel}>
                取消
              </Button>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
