import { Routes, Route, Navigate } from "react-router-dom";
import AppShell from "./components/AppShell";
import DashboardPage from "./pages/DashboardPage";
import KbListPage from "./pages/KbListPage";
import KbLayout from "./pages/KbLayout";
import KbOverviewPage from "./pages/KbOverviewPage";
import DocumentPage from "./pages/DocumentPage";
import DocumentDetailPage from "./pages/DocumentDetailPage";
import EntityRelationPage from "./pages/EntityRelationPage";
import GraphPage from "./pages/GraphPage";
import KbJobsPage from "./pages/KbJobsPage";
import JobDetailPage from "./pages/JobDetailPage";
import QueryPage from "./pages/QueryPage";
import KbCostPage from "./pages/KbCostPage";
import JobsPage from "./pages/JobsPage";
import CostPage from "./pages/CostPage";
import SystemPage from "./pages/SystemPage";
import DemoPage from "./pages/DemoPage";
import DocumentsCenterPage from "./pages/DocumentsCenterPage";
import GraphCenterPage from "./pages/GraphCenterPage";
import QueryTestPage from "./pages/QueryTestPage";
import ChatPage from "./pages/ChatPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import SettingsPage from "./pages/SettingsPage";
import ProviderProfilesPage from "./pages/ProviderProfilesPage";
import ApiKeysPage from "./pages/ApiKeysPage";
import QueryPresetsPage from "./pages/QueryPresetsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/kbs" element={<KbListPage />} />

        {/* KB workspace: header + tab nav, nested pages share KbContext */}
        <Route path="/kbs/:id" element={<KbLayout />}>
          <Route index element={<KbOverviewPage />} />
          <Route path="documents" element={<DocumentPage />} />
          <Route path="documents/:docId" element={<DocumentDetailPage />} />
          <Route path="documents/:docId/entities" element={<EntityRelationPage />} />
          <Route path="graph" element={<GraphPage />} />
          <Route path="jobs" element={<KbJobsPage />} />
          <Route path="jobs/:jobId" element={<JobDetailPage />} />
          <Route path="query" element={<QueryPage />} />
          <Route path="cost" element={<KbCostPage />} />
        </Route>

        {/* Global aggregators (cross-KB, existing endpoints only) */}
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/cost" element={<CostPage />} />
        <Route path="/system" element={<SystemPage />} />

        {/* Top-level SaaS admin pages (reuse existing endpoints / components) */}
        <Route path="/documents" element={<DocumentsCenterPage />} />
        <Route path="/graph" element={<GraphCenterPage />} />
        <Route path="/query" element={<QueryTestPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/query-presets" element={<QueryPresetsPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/provider-profiles" element={<ProviderProfilesPage />} />
        <Route path="/api-keys" element={<ApiKeysPage />} />

        <Route path="/demo" element={<DemoPage />} />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
