import { useLocation, useNavigate } from "react-router-dom";
import { Home, Ticket, Skull, CalendarDays, Users } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

const NAV = [
  { to: "/", label: "Home", icon: Home, id: "home" },
  { to: "/tiket", label: "Tiket", icon: Ticket, id: "tiket" },
  { to: "/survival", label: "Survival", icon: Skull, id: "survival" },
  { to: "/calendario", label: "Calendario", icon: CalendarDays, id: "calendario" },
  { to: "/giocatori", label: "Giocatori", icon: Users, id: "giocatori" },
];

export default function AppShell({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();

  return (
    <div className="relative min-h-screen pb-24">
      <header className="sticky top-0 z-40 backdrop-blur-xl bg-black/70 border-b border-white/10">
        <div className="mx-auto max-w-5xl px-5 h-16 flex items-center justify-between">
          <button
            data-testid="brand-home-button"
            onClick={() => navigate("/")}
            className="flex items-center gap-3"
          >
            <img src="/icon-192.png" alt="logo" className="h-9 w-9 rounded-sm" />
            <div className="text-left leading-none">
              <div className="font-display text-2xl text-white">SCHEDINA BAR</div>
              <div className="text-[10px] tracking-[0.28em] text-[#00FF66] uppercase">Serie A 2026-27</div>
            </div>
          </button>
          <button
            data-testid="profile-nav-button"
            onClick={() => navigate("/profilo")}
            className="h-9 w-9 rounded-full bg-[#0057B8] text-white text-sm font-bold flex items-center justify-center transition-colors hover:bg-[#00438F]"
          >
            {(user?.nickname || "?").slice(0, 1).toUpperCase()}
          </button>
        </div>
      </header>

      <main className="relative z-10 mx-auto max-w-5xl px-5 py-6 animate-fadeup">{children}</main>

      <nav className="fixed bottom-0 inset-x-0 z-40 backdrop-blur-xl bg-black/80 border-t border-white/10">
        <div className="mx-auto max-w-5xl px-2 grid grid-cols-5">
          {NAV.map(({ to, label, icon: Icon, id }) => {
            const active = location.pathname === to;
            return (
              <button
                key={to}
                data-testid={`nav-${id}`}
                onClick={() => navigate(to)}
                className={`flex flex-col items-center gap-1 py-3 transition-colors duration-200 ${
                  active ? "text-[#00FF66]" : "text-zinc-500 hover:text-white"
                }`}
              >
                <Icon size={20} strokeWidth={active ? 2.4 : 1.8} />
                <span className="text-[10px] tracking-wider uppercase">{label}</span>
              </button>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
