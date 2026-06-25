import { useState } from "react";
import { addDocument, uploadFile, deleteDocument } from "../api/client";
import type { DocumentOut } from "../api/types";

function humanBytes(n: number): string {
  if (!n) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / Math.pow(1024, i);
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function DocumentManager({
  kbId,
  docs,
  reload,
}: {
  kbId: number;
  docs: DocumentOut[];
  reload: () => void;
}) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setBusy(true);
    try {
      await uploadFile(kbId, file);
      reload();
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  };

  const onDelete = async (doc: DocumentOut) => {
    const ok = window.confirm(
      "Delete this document? The graph will NOT shrink — run an incremental job to refresh.",
    );
    if (!ok) return;
    await deleteDocument(kbId, doc.id);
    reload();
  };

  return (
    <div className="space-y-3">
      <ul className="divide-y">
        {docs.map((d) => (
          <li key={d.id} className="flex items-center justify-between py-1">
            <div className="flex flex-col">
              <span className="font-medium">{d.title}</span>
              <span className="text-xs text-gray-500">
                {humanBytes(d.bytes)} · {d.chunk_count} chunks · {d.status ?? "—"}
              </span>
            </div>
            <button
              onClick={() => onDelete(d)}
              className="text-red-600 text-sm border border-red-300 rounded px-2 py-0.5 hover:bg-red-50"
            >
              delete
            </button>
          </li>
        ))}
      </ul>

      <div className="flex flex-col gap-2">
        <label className="text-sm">
          Upload file:
          <input
            aria-label="upload file"
            type="file"
            disabled={busy}
            onChange={onFile}
            className="ml-2"
          />
        </label>

        <form
          onSubmit={async (e) => {
            e.preventDefault();
            await addDocument(kbId, { title: title || "untitled", text });
            setTitle("");
            setText("");
            reload();
          }}
          className="flex flex-col gap-1"
        >
          <input
            className="border p-1"
            placeholder="title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <textarea
            className="border p-1 h-20"
            placeholder="paste text"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <button className="bg-blue-600 text-white px-3 py-1 rounded self-start">
            Add Document
          </button>
        </form>
      </div>
    </div>
  );
}

export default DocumentManager;
