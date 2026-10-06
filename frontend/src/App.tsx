import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import AppLayout from "./layouts/AppLayout";
import Dashboard from "./pages/Dashboard";
import Favoritos from "./pages/Favoritos";
import Historico from "./pages/Historico";
import Configuracoes from "./pages/Configuracoes";
import MeusResultados from "./pages/MeusResultados";

export default function App() {
  return (
      <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <Routes>
          <Route element={<AppLayout />}>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/scanners" element={<Dashboard />} />
            <Route path="/favoritos" element={<Favoritos />} />
            <Route path="/meus-resultados" element={<MeusResultados />} />
            <Route path="/historico" element={<Historico />} />
            <Route path="/configuracoes" element={<Configuracoes />} />
          </Route>
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </BrowserRouter>
  );
}
