import { useState } from "react";
import { createKb } from "../api/client";
import type { KbOut } from "../api/types";

export default function KbForm({ onCreated }: { onCreated: (kb: KbOut) => void }) {
  const [name, setName] = useState("");
  const [method, setMethod] = useState("standard");
  const [settings, setSettings] = useState("{}");
  const [ratio, setRatio] = useState("1.0");
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        const kb = await createKb({
          name,
          method,
          settings_yaml: settings,
          min_unit_success_ratio: parseFloat(ratio),
        });
        onCreated(kb);
      }}
      className="space-y-2"
    >
      <input
        className="border p-1 w-full"
        placeholder="name"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <select
        className="border p-1"
        value={method}
        onChange={(e) => setMethod(e.target.value)}
      >
        <option>standard</option>
        <option>fast</option>
      </select>
      <textarea
        className="border p-1 w-full h-24"
        value={settings}
        onChange={(e) => setSettings(e.target.value)}
        placeholder='{"llm":{"model_provider":"deepseek","model":"deepseek-chat"}}'
      />
      <input
        className="border p-1 w-24"
        type="number"
        step="0.01"
        value={ratio}
        onChange={(e) => setRatio(e.target.value)}
      />
      <button className="bg-blue-600 text-white px-3 py-1 rounded">Create KB</button>
    </form>
  );
}
