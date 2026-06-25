import { Routes, Route, Navigate } from "react-router-dom";
import KbListPage from "./pages/KbListPage";
import KbDetailPage from "./pages/KbDetailPage";
import JobDetailPage from "./pages/JobDetailPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<KbListPage />} />
      <Route path="/kbs/:id" element={<KbDetailPage />} />
      <Route path="/kbs/:id/jobs/:jobId" element={<JobDetailPage />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  );
}
