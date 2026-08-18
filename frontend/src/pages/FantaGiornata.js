import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronLeft, Plus, Users, Shirt, Trash2, Archive, ArchiveRestore } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { BonusBanner } from "@/components/BonusBanner";

export default function FantaGiornata() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const [leagues, setLeagues] = useState([]);
  const [loading, setLoading] = useState(true);
  const [code, setCode] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [showArchived, setShowArchived] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setLeagues(await api("/fg/leagues")); } catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const join = async () => {
    if (!code.trim()) return;
    try {
      const prev = await api(`/fg/leagues/by-code/${code.trim().toUpperCase()}`);
      await api(`/fg/leagues/${prev.id}/join`, { method: "POST", body: { invite_code: code.trim().toUpperCase() } });
      toast.success("Iscritto alla lega!");
      setCode("");
      navigate(`/fanta/${prev.id}`);
    } catch (e) { toast.error(e.message); }
  };

  const create = async () => {
    try {
      const lg = await api("/fg/leagues", { method: "POST", body: { name } });
      toast.success("Lega creata!");
      setCreating(false); setName("");
      navigate(`/fanta/${lg.id}`);
    } catch (e) { toast.error(e.message); }
  };

  const remove = async (e, lg) => {
    e.stopPropagation();
    if (!window.confirm(`Eliminare la lega "${lg.name}"? Verranno rimossi iscritti, formazioni e punteggi. Operazione irreversibile.`)) return;
    try { await api(`/fg/leagues/${lg.id}`, { method: "DELETE" }); toast.success("Lega eliminata"); load(); }
    catch (err) { toast.error(err.message); }
  };

  const archive = async (e, lg) => {
    e.stopPropagation();
    if (!window.confirm(`Archiviare la lega "${lg.name}"? Resterà consultabile nella sezione Archiviate.`)) return;
    try { await api(`/fg/leagues/${lg.id}/archive?archived=true`, { method: "POST" }); toast.success("Lega archiviata"); load(); }
    catch (err) { toast.error(err.message); }
  };
  const unarchive = async (e, lg) => {
    e.stopPropagation();
    try { await api(`/fg/leagues/${lg.id}/archive?archived=false`, { method: "POST" }); toast.success("Lega ripristinata"); load(); }
    catch (err) { toast.error(err.message); }
  };

  const activeLeagues = leagues.filter((lg) => !lg.archived);
  const archivedLeagues = leagues.filter((lg) => lg.archived);

  return (
    <div className="space-y-5">
      <button data-testid="fg-back" onClick={() => navigate("/")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Hub</button>
      <BonusBanner game="fanta" />
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-extrabold text-[#A855F7]">FantaGiornata</h1>
          <p className="text-[#94A3B8] text-sm">Schiera la tua formazione giornata per giornata e sfida la lega.</p>
        </div>
        {isAdmin && (
          <button data-testid="fg-new" onClick={() => setCreating((v) => !v)} className="text-sm bg-white/10 hover:bg-white/20 border border-white/15 rounded-md px-3 py-2 flex items-center gap-1.5"><Plus size={16} /> Nuova</button>
        )}
      </div>

      {isAdmin && creating && (
        <div className="rounded-xl border border-[#A855F7]/40 bg-[#A855F7]/5 p-4 space-y-3">
          <input data-testid="fg-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome lega" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
          <button data-testid="fg-create" onClick={create} disabled={!name} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Crea lega</button>
        </div>
      )}

      <div className="flex gap-2">
        <input data-testid="fg-join-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="Codice invito" className="flex-1 bg-[#181D22] border border-white/15 rounded-md px-4 py-3 text-sm" />
        <button data-testid="fg-join" onClick={join} className="bg-[#00D95F] text-[#08110A] font-bold rounded-md px-5">Entra</button>
      </div>

      {loading ? <div className="py-12 text-center text-[#94A3B8]">Caricamento...</div>
      : activeLeagues.length === 0 ? <div className="rounded-xl border border-white/10 bg-[#181D22] p-8 text-center text-[#94A3B8]">Nessuna lega. Usa un codice invito per entrare.</div>
      : (
        <div className="space-y-3">
          {activeLeagues.map((lg) => (
            <div key={lg.id} data-testid={`fg-card-${lg.id}`} className="w-full rounded-xl border border-white/10 bg-[#181D22] p-4 hover:border-[#A855F7]/60 transition-colors flex items-center gap-3">
              <button onClick={() => navigate(`/fanta/${lg.id}`)} className="flex-1 min-w-0 text-left flex items-center gap-3">
                <div className="w-11 h-11 rounded-lg bg-[#A855F7]/20 flex items-center justify-center shrink-0"><Shirt size={22} className="text-[#A855F7]" /></div>
                <div className="flex-1 min-w-0">
                  <div className="text-lg font-extrabold">{lg.name}</div>
                  <div className="flex items-center gap-3 mt-0.5 text-sm text-[#94A3B8]">
                    <span className="flex items-center gap-1"><Users size={13} /> {lg.members_count}</span>
                    {lg.is_admin && <span className="text-[#F59E0B] text-xs">admin</span>}
                  </div>
                </div>
              </button>
              {isAdmin && lg.current_matchday && (
                <button data-testid={`fg-archive-${lg.id}`} onClick={(e) => archive(e, lg)} title="Archivia lega" className="p-2 rounded-md text-[#F59E0B] hover:bg-[#F59E0B]/10 shrink-0"><Archive size={18} /></button>
              )}
              {isAdmin && (
                <button data-testid={`fg-delete-${lg.id}`} onClick={(e) => remove(e, lg)} title="Elimina lega" className="p-2 rounded-md text-[#EF4444] hover:bg-[#EF4444]/10 shrink-0"><Trash2 size={18} /></button>
              )}
            </div>
          ))}
        </div>
      )}

      {isAdmin && archivedLeagues.length > 0 && (
        <div className="space-y-3">
          <button data-testid="fg-archived-toggle" onClick={() => setShowArchived((v) => !v)} className="flex items-center gap-2 text-sm text-[#94A3B8] hover:text-white transition-colors">
            <Archive size={15} /> Archiviate ({archivedLeagues.length}) {showArchived ? "▲" : "▼"}
          </button>
          {showArchived && archivedLeagues.map((lg) => (
            <div key={lg.id} data-testid={`fg-archived-${lg.id}`} className="w-full rounded-xl border border-white/5 bg-[#12161A] p-4 flex items-center gap-2 opacity-80">
              <button onClick={() => navigate(`/fanta/${lg.id}`)} className="flex-1 min-w-0 text-left">
                <span className="font-bold">{lg.name}</span>
                <span className="ml-2 text-xs text-[#94A3B8]">archiviata</span>
              </button>
              <button data-testid={`fg-unarchive-${lg.id}`} onClick={(e) => unarchive(e, lg)} title="Ripristina" className="p-2 rounded-md text-[#00D95F] hover:bg-white/5 shrink-0"><ArchiveRestore size={18} /></button>
              <button data-testid={`fg-delete-${lg.id}`} onClick={(e) => remove(e, lg)} title="Elimina" className="p-2 rounded-md text-[#EF4444] hover:bg-white/5 shrink-0"><Trash2 size={18} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
