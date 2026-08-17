import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import AppShell from "@/components/AppShell";
import Login from "@/pages/Login";
import Hub from "@/pages/Hub";
import Survival from "@/pages/Survival";
import SurvivalDetail from "@/pages/SurvivalDetail";
import Tiket from "@/pages/Tiket";
import TiketRoom from "@/pages/TiketRoom";
import ScoreAndLive from "@/pages/ScoreAndLive";
import ScoreAndLiveDetail from "@/pages/ScoreAndLiveDetail";
import Bonus from "@/pages/Bonus";
import Admin from "@/pages/Admin";
import Settings from "@/pages/Settings";

function Loader() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="h-10 w-10 rounded-full border-2 border-white/10 border-t-[#F59E0B] animate-spin" />
    </div>
  );
}

function Protected({ children }) {
  const { user, ready } = useAuth();
  if (!ready) return <Loader />;
  if (!user) return <Navigate to="/login" replace />;
  return <AppShell>{children}</AppShell>;
}

function LoginGate() {
  const { user, ready } = useAuth();
  if (!ready) return <Loader />;
  if (user) return <Navigate to="/" replace />;
  return <Login />;
}

function App() {
  return (
    <div className="App">
      <div className="app-bg" style={{ backgroundImage: `url(${process.env.PUBLIC_URL}/stadium-bg.webp)` }} />
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginGate />} />
            <Route path="/" element={<Protected><Hub /></Protected>} />
            <Route path="/survival" element={<Protected><Survival /></Protected>} />
            <Route path="/survival/:tid" element={<Protected><SurvivalDetail /></Protected>} />
            <Route path="/tiket" element={<Protected><Tiket /></Protected>} />
            <Route path="/tiket/:roomId" element={<Protected><TiketRoom /></Protected>} />
            <Route path="/scoreandlive" element={<Protected><ScoreAndLive /></Protected>} />
            <Route path="/scoreandlive/:tid" element={<Protected><ScoreAndLiveDetail /></Protected>} />
            <Route path="/bonus" element={<Protected><Bonus /></Protected>} />
            <Route path="/admin" element={<Protected><Admin /></Protected>} />
            <Route path="/settings" element={<Protected><Settings /></Protected>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
        <Toaster theme="dark" position="top-center" richColors />
      </AuthProvider>
    </div>
  );
}

export default App;
