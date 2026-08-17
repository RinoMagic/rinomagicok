import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import AppShell from "@/components/AppShell";
import Login from "@/pages/Login";
import Home from "@/pages/Home";
import Tiket from "@/pages/Tiket";
import Survival from "@/pages/Survival";
import Calendario from "@/pages/Calendario";
import Giocatori from "@/pages/Giocatori";
import Profilo from "@/pages/Profilo";

function Loader() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="h-10 w-10 rounded-full border-2 border-white/10 border-t-[#0057B8] animate-spin" />
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
    <div className="App noise">
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginGate />} />
            <Route path="/" element={<Protected><Home /></Protected>} />
            <Route path="/tiket" element={<Protected><Tiket /></Protected>} />
            <Route path="/survival" element={<Protected><Survival /></Protected>} />
            <Route path="/calendario" element={<Protected><Calendario /></Protected>} />
            <Route path="/giocatori" element={<Protected><Giocatori /></Protected>} />
            <Route path="/profilo" element={<Protected><Profilo /></Protected>} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
        <Toaster theme="dark" position="top-center" richColors />
      </AuthProvider>
    </div>
  );
}

export default App;
