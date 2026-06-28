/** Sidebar navigation config — grouped, Chinese (中文 SaaS admin IA). */
import type { ComponentType, SVGProps } from "react";
import {
  IconDashboard,
  IconDatabase,
  IconDoc,
  IconGraph,
  IconSearch,
  IconChat,
  IconChart,
  IconTask,
  IconCost,
  IconPulse,
  IconGear,
  IconKey,
} from "../components/icons";

export interface NavItem {
  to: string;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    title: "工作台",
    items: [{ to: "/", label: "概览", icon: IconDashboard }],
  },
  {
    title: "知识库",
    items: [
      { to: "/kbs", label: "知识库管理", icon: IconDatabase },
      { to: "/documents", label: "文档管理", icon: IconDoc },
      { to: "/graph", label: "图谱管理", icon: IconGraph },
    ],
  },
  {
    title: "检索与问答",
    items: [
      { to: "/query", label: "检索测试", icon: IconSearch },
      { to: "/chat", label: "问答对话", icon: IconChat },
    ],
  },
  {
    title: "分析与监控",
    items: [
      { to: "/analytics", label: "分析报表", icon: IconChart },
      { to: "/jobs", label: "任务管理", icon: IconTask },
      { to: "/cost", label: "成本统计", icon: IconCost },
    ],
  },
  {
    title: "系统管理",
    items: [
      { to: "/system", label: "系统状态", icon: IconPulse },
      { to: "/settings", label: "系统设置", icon: IconGear },
      { to: "/provider-profiles", label: "Provider 配置", icon: IconKey },
      { to: "/api-keys", label: "API Keys", icon: IconKey },
    ],
  },
];
