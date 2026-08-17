import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronLeft, Bell, LogOut, Shield, User, Send } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { api } from "@/lib/api";
import { enablePush, pushSupported, testPush } from "@/push";

export default function Settings() {
  const navigate = useNavigate();
  const { user, logout, isAdmin } = useAuth();
  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");

  const doPush = async () => {
    try { await enablePush(); toast.success("Notifiche attivate!"); }
    catch (e) { toast.error(e.message); }
  };
  const doTest = async () => {
    try { await testPush(); toast.success("Notifica di test inviata!"); }
    catch (e) { toast.error(e.message); }
  };
  const changePw = async () => {
    try {
      await api("/auth/admin/change-password", { method: "POST", body: { old_password: oldPw, new_password: newPw } });
      toast.success("Password aggiornata!"); setOldPw(""); setNewPw("");
    } catch (e) { toast.error(e.message); }
  };

  return (
    <div className="space-y-5 max-w-lg">
      <button data-testid="set-back" onClick={() => navigate("/")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Hub</button>

      <h1 className="text-2xl font-extrabold">Impostazioni</h1>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-5">
        <div className="flex items-center gap-4">
          <div className="h-14 w-14 rounded-xl bg-[#F59E0B] text-[#1A1000] text-2xl font-black flex items-center justify-center">
            {(user?.username || user?.email || "?").slice(0, 1).toUpperCase()}
          </div>
          <div>
            <div className="text-xl font-extrabold">{user?.username || user?.email?.split("@")[0]}</div>
            <div className="text-sm text-[#94A3B8] flex items-center gap-1.5 mt-0.5">
              {isAdmin ? <Shield size={14} className="text-[#F59E0B]" /> : <User size={14} />}
              {isAdmin ? "Amministratore" : "Giocatore"}
            </div>
          </div>
        </div>
      </div>

      {pushSupported() && (
        <div className="rounded-xl border border-white/10 bg-[#181D22] p-5 space-y-3">
          <div className="font-bold flex items-center gap-2"><Bell size={18} className="text-[#F59E0B]" /> Notifiche push</div>
          <div className="flex gap-2">
            <button data-testid="set-enable-push" onClick={doPush} className="flex-1 bg-[#F59E0B] text-[#1A1000] font-bold rounded-md py-2.5">Attiva</button>
            <button data-testid="set-test-push" onClick={doTest} className="px-4 border border-white/15 rounded-md flex items-center gap-2 text-sm"><Send size={15} /> Test</button>
          </div>
        </div>
      )}

      {isAdmin && (
        <div className="rounded-xl border border-white/10 bg-[#181D22] p-5 space-y-3">
          <div className="font-bold">Cambia password</div>
          <input data-testid="set-oldpw" type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} placeholder="Password attuale" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
          <input data-testid="set-newpw" type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} placeholder="Nuova password (min 8)" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
          <button data-testid="set-changepw" onClick={changePw} disabled={!oldPw || newPw.length < 8} className="bg-white/10 border border-white/15 rounded-md px-4 py-2 text-sm disabled:opacity-50">Aggiorna</button>
        </div>
      )}

      <button data-testid="set-logout" onClick={logout} className="w-full rounded-xl border border-[#EF4444]/40 bg-[#EF4444]/10 text-[#EF4444] p-4 flex items-center justify-center gap-2 font-bold">
        <LogOut size={18} /> Esci
      </button>
    </div>
  );
}
