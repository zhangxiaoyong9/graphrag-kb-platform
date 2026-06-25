import { useState } from "react";
import { addDocument } from "../api/client";

export default function DocumentUpload({ kbId, onUploaded }: { kbId: number; onUploaded: () => void }) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  return (
    <form onSubmit={async (e) => { e.preventDefault(); await addDocument(kbId, { title: title || "untitled", text }); setTitle(""); setText(""); onUploaded(); }} className="space-y-2">
      <input className="border p-1 w-full" placeholder="title" value={title} onChange={(e) => setTitle(e.target.value)} />
      <textarea className="border p-1 w-full h-24" placeholder="paste text" value={text} onChange={(e) => setText(e.target.value)} />
      <button className="bg-blue-600 text-white px-3 py-1 rounded">Add Document</button>
    </form>
  );
}
