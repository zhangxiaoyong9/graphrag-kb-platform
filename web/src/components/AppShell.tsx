import { useEffect, useState } from "react";
import type { ComponentType, SVGProps } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { NAV_GROUPS } from "../lib/nav";
import { cn } from "../lib/cn";
import { getHealth } from "../api/client";
import type { Health } from "../api/types";
import { statusTone, statusLabel } from "../lib/status";
import { Badge } from "./ui";
import {
  IconGraph,
  IconMenu,
  IconExternal,
  IconPulse,
} from "./icons";

/** Top-level chrome: collapsible sidebar + top bar + routed content. */
export default function AppShell() {
  const [open, setOpen] = useState(false); // mobile sidebar
  const loc = useLocation();
  useEffect(() => {
    setOpen(false);
  }, [loc.pathname]);

  return (
    <div className="min-h-screen lg:flex">
      <Sidebar open={open} onClose={() => setOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar onMenu={() => setOpen(true)} />
        <main className="flex-1 px-4 py-5 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-7xl animate-fade-in">
            <Outlet />
          </div>
        </main>
        <footer className="px-6 py-4 text-center text-xs text-muted">
          KB Platform · 基于 Microsoft GraphRAG 构建
        </footer>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- Sidebar */

function BrandMark() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-grad text-white shadow-brand">
        <IconGraph width={20} height={20} />
      </span>
      <div className="leading-tight">
        <div className="text-[15px] font-semibold text-ink">知识库平台</div>
        <div className="text-[11px] text-muted">GraphRAG Control Plane</div>
      </div>
    </div>
  );
}

function NavRow({ item }: { item: { to: string; label: string; icon: ComponentType<SVGProps<SVGSVGElement>> } }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.to === "/"}
      className={({ isActive }) => cn("nav-link", isActive && "nav-link-active")}
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-brand" />
          )}
          <Icon width={18} height={18} />
          <span>{item.label}</span>
        </>
      )}
    </NavLink>
  );
}

function Sidebar({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-ink/30 backdrop-blur-sm lg:hidden"
          onClick={onClose}
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-[248px] flex-col border-r border-line bg-surface transition-transform lg:static lg:z-auto lg:translate-x-0",
          open ? "translate-x-0 shadow-pop" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 items-center px-5">
          <BrandMark />
        </div>
        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-2">
          {NAV_GROUPS.map((group) => (
            <div key={group.title} className="space-y-1">
              <p className="px-3 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-muted">
                {group.title}
              </p>
              {group.items.map((item) => (
                <NavRow key={item.to} item={item} />
              ))}
            </div>
          ))}
        </nav>
        <div className="border-t border-line p-3">
          <NavLink
            to="/demo"
            className={({ isActive }) => cn("nav-link", isActive && "nav-link-active")}
          >
            <IconExternal width={18} height={18} />
            <span>演示预览</span>
          </NavLink>
          <p className="px-3 pt-2 text-[11px] text-muted">v0.1.0 · 内部工具</p>
        </div>
      </aside>
    </>
  );
}

/* ----------------------------------------------------------------- TopBar */

const TITLES: Record<string, string> = {
  "/": "概览",
  "/kbs": "知识库管理",
  "/documents": "文档管理",
  "/graph": "图谱管理",
  "/query": "检索测试",
  "/chat": "问答对话",
  "/analytics": "分析报表",
  "/jobs": "任务管理",
  "/cost": "成本统计",
  "/system": "系统状态",
  "/settings": "系统设置",
  "/api-keys": "API Keys",
  "/demo": "演示预览",
};

function TopBar({ onMenu }: { onMenu: () => void }) {
  const loc = useLocation();
  const title =
    (loc.pathname.startsWith("/kbs") && "知识库") ||
    TITLES[loc.pathname] ||
    "知识库平台";

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center gap-3 border-b border-line bg-surface/85 px-4 backdrop-blur sm:px-6 lg:px-8">
      <button
        className="btn btn-ghost btn-sm px-2 lg:hidden"
        onClick={onMenu}
        aria-label="打开导航"
      >
        <IconMenu width={20} height={20} />
      </button>
      <div className="min-w-0">
        <h2 className="truncate text-[15px] font-semibold text-ink">{title}</h2>
      </div>
      <div className="ml-auto flex items-center gap-3">
        <HealthPill />
      </div>
    </header>
  );
}

function HealthPill() {
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = () =>
      getHealth()
        .then((h) => {
          if (alive) setHealth(h);
        })
        .catch(() => {
          if (alive) setHealth(null);
        });
    tick();
    const h = setInterval(tick, 20000);
    return () => {
      alive = false;
      clearInterval(h);
    };
  }, []);

  const ok = health?.status === "ok";
  const degraded = health?.status === "degraded";
  const tone = health ? statusTone(health.status) : "neutral";
  return (
    <span className="hidden items-center gap-2 rounded-full border border-line bg-surface px-3 py-1.5 text-[12px] sm:flex">
      <IconPulse width={14} height={14} className={ok ? "text-success" : degraded ? "text-warning" : "text-muted"} />
      <span className="text-muted">系统</span>
      <Badge tone={tone} dot>
        {health ? statusLabel(health.status) : "离线"}
      </Badge>
    </span>
  );
}
