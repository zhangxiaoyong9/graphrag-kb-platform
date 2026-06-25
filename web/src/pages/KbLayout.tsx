import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { getKb } from "../api/client";
import type { KbOut } from "../api/types";
import { KbContext } from "./kb-context";
import { cn } from "../lib/cn";
import { Badge, Button } from "../components/ui";
import { ExportButtons } from "../components/kb-actions";
import { IconArrowLeft, IconDoc, IconGraph, IconTask, IconCost, IconSparkle, IconLayers } from "../components/icons";

const TABS = [
  { to: "", label: "概要", icon: IconLayers, end: true },
  { to: "documents", label: "文档", icon: IconDoc, end: false },
  { to: "graph", label: "图谱", icon: IconGraph, end: false },
  { to: "jobs", label: "任务", icon: IconTask, end: false },
  { to: "query", label: "检索问答", icon: IconSparkle, end: false },
  { to: "cost", label: "成本", icon: IconCost, end: false },
];

/** KB workspace shell: header + tab nav + nested page. Provides KbContext. */
export default function KbLayout() {
  const { id } = useParams();
  const kbId = Number(id);
  const [kb, setKb] = useState<KbOut | null>(null);
  const [notFound, setNotFound] = useState(false);

  const reload = useCallback(() => {
    if (!kbId) return;
    getKb(kbId)
      .then(setKb)
      .catch(() => setNotFound(true));
  }, [kbId]);

  useEffect(() => {
    reload();
  }, [reload]);

  if (notFound) {
    return (
      <div className="card card-pad text-center">
        <p className="text-sm text-ink">未找到该知识库。</p>
        <Button className="mx-auto mt-3" variant="secondary" onClick={() => history.back()}>
          返回
        </Button>
      </div>
    );
  }
  if (!kb) return <div className="card card-pad text-sm text-muted">加载中…</div>;

  return (
    <KbContext.Provider value={{ kbId, kb, reload }}>
      <div className="space-y-5">
        {/* Header */}
        <div className="card overflow-hidden">
          <div className="relative bg-brand-grad px-5 py-5 text-white sm:px-6">
            <div className="pointer-events-none absolute -right-10 -top-12 h-40 w-40 rounded-full bg-white/15 blur-2xl" />
            <div className="relative flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <NavLink
                  to="/kbs"
                  className="mb-1 inline-flex items-center gap-1 text-[12px] text-white/80 hover:text-white"
                >
                  <IconArrowLeft width={14} height={14} /> 知识库
                </NavLink>
                <h1 className="flex items-center gap-2 text-xl font-semibold">
                  {kb.name}
                  <Badge className="bg-white/20 text-white">{kb.method}</Badge>
                </h1>
                <p className="mt-1 text-[13px] text-white/80 nums">ID · {kb.id}</p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <ExportButtons kbId={kbId} />
              </div>
            </div>
          </div>

          {/* Tab nav */}
          <nav className="flex gap-5 overflow-x-auto border-b border-line px-4 sm:px-6">
            {TABS.map((t) => {
              const Icon = t.icon;
              return (
                <NavLink
                  key={t.to || "index"}
                  to={t.to}
                  end={t.end}
                  className={({ isActive }) =>
                    cn("tab-link relative flex items-center gap-1.5", isActive && "tab-link-active")
                  }
                >
                  {({ isActive }) => (
                    <>
                      <Icon width={16} height={16} />
                      <span>{t.label}</span>
                      {isActive && (
                        <span className="absolute -bottom-px left-0 right-0 h-0.5 rounded-full bg-brand" />
                      )}
                    </>
                  )}
                </NavLink>
              );
            })}
          </nav>
        </div>

        <Outlet />
      </div>
    </KbContext.Provider>
  );
}

