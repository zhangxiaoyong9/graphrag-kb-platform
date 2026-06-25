import { useState } from "react";
import { createKb } from "../api/client";
import type { KbOut } from "../api/types";
import { Button } from "./ui";
import { Field } from "./ui";
import { IconPlus } from "./icons";

/** Create-knowledge-base form. Posts to POST /kbs, then calls onCreated. */
export default function KbForm({ onCreated }: { onCreated: (kb: KbOut) => void }) {
  const [name, setName] = useState("");
  const [method, setMethod] = useState("standard");
  const [settings, setSettings] = useState("{}");
  const [ratio, setRatio] = useState("1.0");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setError(null);
        try {
          const kb = await createKb({
            name,
            method,
            settings_yaml: settings,
            min_unit_success_ratio: parseFloat(ratio),
          });
          onCreated(kb);
          setName("");
          setSettings("{}");
          setRatio("1.0");
        } catch (err) {
          setError(String((err as Error).message ?? err));
        } finally {
          setBusy(false);
        }
      }}
      className="space-y-3"
    >
      <Field label="知识库名称">
        <input
          className="input"
          placeholder="请输入知识库名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="索引方法" hint="standard / fast">
          <select className="select" value={method} onChange={(e) => setMethod(e.target.value)}>
            <option value="standard">standard（LLM 精抽取）</option>
            <option value="fast">fast（NLP 快速）</option>
          </select>
        </Field>
        <Field label="最小成功率" hint="低于此值步骤失败">
          <input
            className="input"
            type="number"
            step="0.01"
            min="0"
            max="1"
            value={ratio}
            onChange={(e) => setRatio(e.target.value)}
          />
        </Field>
      </div>
      <Field label="模型设置 (settings_yaml)" hint="如 DeepSeek / OpenAI 配置，密钥从环境变量读取">
        <textarea
          className="textarea h-24 font-mono text-[12px]"
          value={settings}
          onChange={(e) => setSettings(e.target.value)}
          placeholder='{"llm":{"model_provider":"deepseek","model":"deepseek-chat"}}'
        />
      </Field>
      {error && <p className="text-[13px] text-danger">创建失败：{error}</p>}
      <Button type="submit" variant="primary" disabled={busy} className="w-full">
        <IconPlus width={16} height={16} />
        {busy ? "创建中…" : "创建知识库"}
      </Button>
    </form>
  );
}
