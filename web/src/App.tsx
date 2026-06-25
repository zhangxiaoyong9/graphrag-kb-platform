import { Routes, Route, Navigate } from "react-router-dom";
import KbListPage from "./pages/KbListPage";
import KbDetailPage from "./pages/KbDetailPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<KbListPage />} />
      <Route path="/kbs/:id" element={<KbDetailPage />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  );
}
