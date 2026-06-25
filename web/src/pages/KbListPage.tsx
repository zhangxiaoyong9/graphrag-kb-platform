import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listKbs } from "../api/client";
import type { KbOut } from "../api/types";
import KbForm from "../components/KbForm";

export default function KbListPage() {
  const [kbs, setKbs] = useState<KbOut[]>([]);
  const reload = () => listKbs().then(setKbs);
  useEffect(() => {
    reload();
  }, []);
  return (
    <div className="p-4 space-y-4">
      <h1 className="text-xl font-bold">Knowledge Bases</h1>
      <KbForm onCreated={reload} />
      <ul className="space-y-1">
        {kbs.map((k) => (
          <li key={k.id}>
            <Link to={`/kbs/${k.id}`} className="text-blue-600 underline">
              {k.name}
            </Link>{" "}
            <span className="text-gray-500">({k.method})</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
