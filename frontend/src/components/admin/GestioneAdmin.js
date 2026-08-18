import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { ShieldCheck, Trash2, UserPlus } from "lucide-react";

// Gestione Admin: crea/promuovi o rimuovi altri amministratori.
export default function GestioneAdmin() {
  const [users, setUsers] = useState([]);
  const [email, setEmail] = useState("");
  const [tempPw, setTempPw] = useState("");

  const load = () => api("/auth/users").then(setUsers).catch((e) => toast.error(e.message));
  useEffect(() => { load(); }, []);

  const admins = users.filter((u) => u.role === "admin");

  const promote = async () => {
    if (!email.trim() || tempPw.length < 8) { toast.error("Email valida e password (min 8)"); return; }
    try {
      await api("/auth/admin/promote", { method: "POST", body: { email: email.trim(), temp_password: tempPw } });
      toast.success("Admin creato/promosso (dovrà cambiare password al primo accesso)");
      setEmail(""); setTempPw("");
      load();
    } catch (e) { toast.error(e.message); }
  };

  const remove = async (u) => {
    if (!window.confirm(`Rimuovere l'admin ${u.email || u.username}?`)) return;
    try { await api(`/auth/users/${u.id}`, { method: "DELETE" }); toast.success("Admin rimosso"); load(); }
    catch (e) { toast.error(e.message); }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-[#EC4899]/40 bg-[#EC4899]/5 p-4 space-y-3">
        <div className="font-bold flex items-center gap-2"><UserPlus size={18} className="text-[#EC4899]" /> Nuovo amministratore</div>
        <p className="text-xs text-[#94A3B8]">Se l'email appartiene a un giocatore esistente verrà promosso, altrimenti si crea un nuovo account admin.</p>
        <input data-testid="ga-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email admin" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
        <input data-testid="ga-temppw" type="text" value={tempPw} onChange={(e) => setTempPw(e.target.value)} placeholder="Password temporanea (min 8)" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
        <button data-testid="ga-promote" onClick={promote} className="bg-[#EC4899] text-white font-bold text-sm rounded-md px-4 py-2">Crea / Promuovi admin</button>
      </div>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-2">
        <div className="font-bold flex items-center gap-2"><ShieldCheck size={18} className="text-[#EC4899]" /> Amministratori ({admins.length})</div>
        {admins.map((u) => (
          <div key={u.id} data-testid={`ga-admin-${u.id}`} className="rounded-md bg-[#0F1216] border border-white/10 px-3 py-2 flex items-center gap-2">
            <span className="flex-1 truncate text-sm">{u.email || u.username}</span>
            <button data-testid={`ga-del-${u.id}`} onClick={() => remove(u)} className="p-1.5 text-[#EF4444] hover:bg-white/5 rounded"><Trash2 size={15} /></button>
          </div>
        ))}
      </div>
    </div>
  );
}
