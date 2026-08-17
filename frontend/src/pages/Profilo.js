import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { enablePush, pushSupported } from "@/push";
import { LogOut, Bell, Shield, User } from "lucide-react";

export default function Profilo() {
  const { user, logout } = useAuth();

  const doEnablePush = async () => {
    try {
      await enablePush();
      toast.success("Notifiche attivate!");
    } catch (e) {
      toast.error(e.message);
    }
  };

  return (
    <div className="space-y-6 max-w-lg">
      <h1 className="font-display text-4xl">PROFILO</h1>

      <div className="rounded-sm border border-white/10 bg-[#141414] p-6">
        <div className="flex items-center gap-4">
          <div className="h-16 w-16 rounded-sm bg-[#0057B8] text-white text-2xl font-display flex items-center justify-center">
            {(user?.nickname || "?").slice(0, 1).toUpperCase()}
          </div>
          <div>
            <div className="font-display text-3xl leading-none">{user?.nickname}</div>
            <div className="text-sm text-zinc-400 mt-1 flex items-center gap-2">
              {user?.role === "admin" ? <Shield size={14} className="text-[#00FF66]" /> : <User size={14} />}
              {user?.role === "admin" ? "Amministratore" : "Giocatore"}
            </div>
          </div>
        </div>
        {user?.email && <div className="mt-4 text-sm text-zinc-500">{user.email}</div>}
      </div>

      {pushSupported() && (
        <button
          data-testid="profile-enable-push"
          onClick={doEnablePush}
          className="w-full rounded-sm border border-white/10 bg-[#141414] p-5 flex items-center justify-between hover:border-white/30 transition-colors duration-200"
        >
          <span className="flex items-center gap-3"><Bell size={18} className="text-[#0057B8]" /> Attiva notifiche push</span>
        </button>
      )}

      <button
        data-testid="logout-button"
        onClick={logout}
        className="w-full rounded-sm border border-[#E32221]/40 bg-[#E32221]/10 text-[#E32221] p-5 flex items-center justify-center gap-2 hover:bg-[#E32221]/20 transition-colors duration-200 font-semibold"
      >
        <LogOut size={18} /> Esci
      </button>
    </div>
  );
}
