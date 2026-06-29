import { useEffect, useState } from "react";
import { listQueryPresets, createQueryPreset, deleteQueryPreset } from "../api/client";
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
};

/** 检索预设：全局跨 KB 的查询配置库；内置只读。 */
export default function QueryPresetsPage() {
  const [items, setItems] = useState<QueryPreset[] | null>(null);
  const [draft, setDraft] = useState<Draft>(BLANK);

  const reload = () => listQueryPresets().then(setItems).catch(() => setItems([]));
  useEffect(() => {
    reload();
  }, []);

  const create = async () => {
    if (!draft.name.trim()) return;
    await createQueryPreset(draft);
    setDraft(BLANK);
    reload();
  };

  const remove = async (id: number) => {
    await deleteQueryPreset(id);
    reload();
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
              hint="在下方新建，或在检索页「另存为预设」。"
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
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => (
                  <tr key={p.id} className="border-t border-line">
                    <td className="py-2 font-medium text-ink">
                      {p.name}
                      {p.is_builtin && <span className="ml-1 text-[10px] text-muted">内置</span>}
                    </td>
                    <td className="font-mono text-[12px]">{p.method}</td>
                    <td>{p.community_level ?? "—"}</td>
                    <td>{p.response_type ?? "—"}</td>
                    <td>{p.top_k ?? "—"}</td>
                    <td>{p.temperature ?? "—"}</td>
                    <td>
                      {!p.is_builtin && (
                        <button
                          className="text-muted hover:text-danger"
                          title="删除"
                          aria-label={`删除 ${p.name}`}
                          onClick={() => remove(p.id)}
                        >
                          <IconTrash width={14} height={14} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      <Card>
        <CardHeader title="新建预设" icon={<IconPlus width={18} height={18} />} />
        <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-3">
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
          </select>
          <input
            className="input"
            type="number"
            placeholder="community_level（可空）"
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
            <option value="">response_type：默认</option>
            <option value="multiple paragraphs">多段</option>
            <option value="single paragraph">单段</option>
            <option value="bullet points">要点</option>
          </select>
          <input
            className="input"
            type="number"
            placeholder="top_k（可空）"
            value={draft.top_k ?? ""}
            onChange={(e) => setDraft({ ...draft, top_k: e.target.value ? Number(e.target.value) : null })}
          />
          <input
            className="input"
            type="number"
            step="0.05"
            placeholder="temperature（可空）"
            value={draft.temperature ?? ""}
            onChange={(e) =>
              setDraft({ ...draft, temperature: e.target.value ? Number(e.target.value) : null })
            }
          />
        </div>
        <div className="mt-3">
          <Button variant="primary" onClick={create}>
            <IconPlus width={16} height={16} />
            新建
          </Button>
        </div>
      </Card>
    </div>
  );
}
