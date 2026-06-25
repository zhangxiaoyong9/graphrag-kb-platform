import { Routes, Route, Navigate } from "react-router-dom";
import KbListPage from "./pages/KbListPage";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<KbListPage />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  );
}
