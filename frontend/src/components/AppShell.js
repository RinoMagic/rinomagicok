import { useNavigate } from "react-router-dom";
import { Settings, LogOut } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

export default function AppShell({ children }) {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const name = user?.username || user?.email?.split("@")[0] || "";

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 backdrop-blur-xl bg-[#0F1216]/70 border-b border-white/10">
        <div className="mx-auto max-w-3xl px-4 h-16 flex items-center gap-3">
          <button data-testid="brand-home" onClick={() => navigate("/")} className="flex items-center gap-3 flex-1 min-w-0">
            <img src="/barslot-logo.jpg" alt="RinoMagic" className="h-9 w-9 rounded-md object-cover" />
            <div className="text-left leading-tight min-w-0">
              <div className="text-lg font-extrabold tracking-wide">RinoMagic</div>
              <div className="text-xs text-[#94A3B8] truncate">Ciao {name}</div>
            </div>
          </button>
          <button data-testid="nav-settings" onClick={() => navigate("/settings")} className="p-2 text-[#F1F5F9] hover:text-[#F59E0B] transition-colors">
            <Settings size={20} />
          </button>
          <button data-testid="nav-logout" onClick={logout} className="p-2 text-[#F1F5F9] hover:text-[#EF4444] transition-colors">
            <LogOut size={20} />
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-6 pb-24 animate-fadeup">{children}</main>
    </div>
  );
}
