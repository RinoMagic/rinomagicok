import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Users, Lock, Unlock, KeyRound, Trash2 } from "lucide-react";

// Gestione Utenti: blocca/sblocca, reset password ed elimina i giocatori.
export default function GestioneUtenti() {
  const [users, setUsers] = useState([]);
  const load = () => api("/auth/users").then(setUsers).catch((e) => toast.error(e.message));
  useEffect(() => { load(); }, []);

  const toggleBlock = async (u) => {
    try { await api(`/auth/users/${u.id}/${u.blocked ? "unblock" : "block"}`, { method: "POST" }); toast.success(u.blocked ? "Sbloccato" : "Bloccato"); load(); }
    catch (e) { toast.error(e.message); }
  };
  const resetPw = async (u) => {
    const pw = window.prompt(`Nuova password per ${u.username || u.email} (min 8):`);
    if (!pw) return;
    if (pw.length < 8) { toast.error("Minimo 8 caratteri"); return; }
    try { await api("/auth/users/reset-password", { method: "POST", body: { user_id: u.id, new_password: pw } }); toast.success("Password reimpostata"); }
    catch (e) { toast.error(e.message); }
  };
  const del = async (u) => {
    if (!window.confirm(`Eliminare definitivamente ${u.username || u.email}? Tutti i suoi dati verranno rimossi.`)) return;
    try { await api(`/auth/users/${u.id}`, { method: "DELETE" }); toast.success("Utente eliminato"); load(); }
    catch (e) { toast.error(e.message); }
  };

  return (
    <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
      <div className="font-bold flex items-center gap-2"><Users size={18} className="text-[#F59E0B]" /> Utenti ({users.length})</div>
      <div className="rounded-lg bg-[#0F1216] border border-white/10 divide-y divide-white/10 max-h-[28rem] overflow-y-auto">
        {users.map((u) => (
          <div key={u.id} data-testid={`gu-user-${u.id}`} className="px-3 py-2.5 flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium truncate flex items-center gap-2">
                {u.username || u.email}
                {u.role === "admin" && <span className="text-[9px] px-1.5 py-0.5 rounded bg-[#F59E0B]/20 text-[#F59E0B]">ADMIN</span>}
                {u.blocked && <span className="text-[9px] px-1.5 py-0.5 rounded bg-[#EF4444]/20 text-[#EF4444]">BLOCCATO</span>}
              </div>
            </div>
            <button data-testid={`gu-reset-${u.id}`} onClick={() => resetPw(u)} title="Reset password" className="p-1.5 text-[#94A3B8] hover:text-white"><KeyRound size={16} /></button>
            <button data-testid={`gu-block-${u.id}`} onClick={() => toggleBlock(u)} title={u.blocked ? "Sblocca" : "Blocca"} className={`p-1.5 ${u.blocked ? "text-[#00D95F]" : "text-[#EF4444]"} hover:brightness-125`}>{u.blocked ? <Unlock size={16} /> : <Lock size={16} />}</button>
            <button data-testid={`gu-del-${u.id}`} onClick={() => del(u)} title="Elimina" className="p-1.5 text-[#EF4444] hover:brightness-125"><Trash2 size={16} /></button>
          </div>
        ))}
      </div>
    </div>
  );
}
