import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Activity, Plus, ChevronLeft, Heart, Trash2, Archive, ArchiveRestore } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { BonusBanner } from "@/components/BonusBanner";

export default function ScoreAndLive() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const [tours, setTours] = useState([]);
  const [loading, setLoading] = useState(true);
  const [code, setCode] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [lives, setLives] = useState(3);
  const [startMd, setStartMd] = useState(1);
  const [showArchived, setShowArchived] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setTours(await api("/sal/tournaments")); } catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const join = async () => {
    if (!code.trim()) return;
    try {
      const prev = await api(`/sal/tournaments/by-code/${code.trim().toUpperCase()}`);
      await api(`/sal/tournaments/${prev.id}/join`, { method: "POST", body: { invite_code: code.trim().toUpperCase() } });
      toast.success("Iscritto!");
      setCode("");
      navigate(`/scoreandlive/${prev.id}`);
    } catch (e) { toast.error(e.message); }
  };

  const create = async () => {
    try {
      const t = await api("/sal/tournaments", { method: "POST", body: { name, initial_lives: Number(lives), start_matchday: Number(startMd) } });
      toast.success("Torneo creato!");
      setCreating(false); setName("");
      navigate(`/scoreandlive/${t.id}`);
    } catch (e) { toast.error(e.message); }
  };

  const remove = async (e, t) => {
    e.stopPropagation();
    if (!window.confirm(`Eliminare il torneo "${t.name}"? Verranno rimossi iscritti, giornate e pronostici. Operazione irreversibile.`)) return;
    try {
      await api(`/sal/tournaments/${t.id}`, { method: "DELETE" });
      toast.success("Torneo eliminato"); load();
    } catch (err) {
      if (String(err.message || "").includes("storiche") || String(err.message || "").includes("force")) {
        if (window.confirm("Il torneo contiene giocate storiche. Eliminarlo cancellerà lo storico. Confermi comunque?")) {
          try { await api(`/sal/tournaments/${t.id}?force=true`, { method: "DELETE" }); toast.success("Torneo eliminato"); load(); }
          catch (e2) { toast.error(e2.message); }
        }
        return;
      }
      toast.error(err.message);
    }
  };

  const archive = async (e, t) => {
    e.stopPropagation();
    if (!window.confirm(`Archiviare il torneo "${t.name}"? Resterà consultabile nella sezione Archiviati.`)) return;
    try { await api(`/sal/tournaments/${t.id}/archive?archived=true`, { method: "POST" }); toast.success("Torneo archiviato"); load(); }
    catch (err) { toast.error(err.message); }
  };
  const unarchive = async (e, t) => {
    e.stopPropagation();
    try { await api(`/sal/tournaments/${t.id}/archive?archived=false`, { method: "POST" }); toast.success("Torneo ripristinato"); load(); }
    catch (err) { toast.error(err.message); }
  };

  const activeTours = tours.filter((t) => !t.archived);
  const archivedTours = tours.filter((t) => t.archived);

  return (
    <div className="space-y-5">
      <button data-testid="sal-back" onClick={() => navigate("/")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Hub</button>
      <BonusBanner game="score" />
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-extrabold text-[#3B82F6]">ScoreAndLive</h1>
          <p className="text-[#94A3B8] text-sm">Indovina i marcatori e sopravvivi giornata dopo giornata.</p>
        </div>
        {isAdmin && (
          <button data-testid="sal-new" onClick={() => setCreating((v) => !v)} className="text-sm bg-white/10 hover:bg-white/20 border border-white/15 rounded-md px-3 py-2 flex items-center gap-1.5"><Plus size={16} /> Nuovo</button>
        )}
      </div>

      {isAdmin && creating && (
        <div className="rounded-xl border border-[#3B82F6]/40 bg-[#3B82F6]/5 p-4 space-y-3">
          <input data-testid="sal-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome torneo" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-[#94A3B8]">Vite<input data-testid="sal-lives" type="number" min="1" max="15" value={lives} onChange={(e) => setLives(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" /></label>
            <label className="text-xs text-[#94A3B8]">Giornata iniziale<input data-testid="sal-startmd" type="number" min="1" max="38" value={startMd} onChange={(e) => setStartMd(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" /></label>
          </div>
          <button data-testid="sal-create" onClick={create} disabled={!name} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Crea</button>
        </div>
      )}

      <div className="flex gap-2">
        <input data-testid="sal-join-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="Codice invito" className="flex-1 bg-[#181D22] border border-white/15 rounded-md px-4 py-3 text-sm" />
        <button data-testid="sal-join" onClick={join} className="bg-[#00D95F] text-[#08110A] font-bold rounded-md px-5">Entra</button>
      </div>

      {loading ? <div className="py-12 text-center text-[#94A3B8]">Caricamento...</div>
      : activeTours.length === 0 ? <div className="rounded-xl border border-white/10 bg-[#181D22] p-8 text-center text-[#94A3B8]">Nessun torneo attivo.</div>
      : (
        <div className="space-y-3">
          {activeTours.map((t) => (
            <div key={t.id} data-testid={`sal-card-${t.id}`} className="w-full rounded-xl border border-white/10 bg-[#181D22] p-4 flex items-start gap-2 hover:border-[#3B82F6]/60 transition-colors">
              <button onClick={() => navigate(`/scoreandlive/${t.id}`)} className="flex-1 min-w-0 text-left">
                <div className="flex items-center justify-between">
                  <span className="text-lg font-extrabold">{t.name}</span>
                  <Activity size={18} className="text-[#3B82F6]" />
                </div>
                <div className="flex items-center gap-4 mt-1 text-sm text-[#94A3B8]">
                  <span className="flex items-center gap-1 text-[#EF4444]"><Heart size={14} /> {t.players_alive ?? "?"}/{t.players_total ?? "?"}</span>
                  {t.status === "finished" && <span className="text-[#F59E0B]">Concluso</span>}
                </div>
              </button>
              {isAdmin && t.status === "finished" && (
                <button data-testid={`sal-archive-${t.id}`} onClick={(e) => archive(e, t)} title="Archivia torneo" className="p-2 rounded-md text-[#F59E0B] hover:bg-[#F59E0B]/10 shrink-0"><Archive size={18} /></button>
              )}
              {isAdmin && (
                <button data-testid={`sal-delete-${t.id}`} onClick={(e) => remove(e, t)} title="Elimina torneo" className="p-2 rounded-md text-[#EF4444] hover:bg-[#EF4444]/10 shrink-0"><Trash2 size={18} /></button>
              )}
            </div>
          ))}
        </div>
      )}

      {isAdmin && archivedTours.length > 0 && (
        <div className="space-y-3">
          <button data-testid="sal-archived-toggle" onClick={() => setShowArchived((v) => !v)} className="flex items-center gap-2 text-sm text-[#94A3B8] hover:text-white transition-colors">
            <Archive size={15} /> Archiviati ({archivedTours.length}) {showArchived ? "▲" : "▼"}
          </button>
          {showArchived && archivedTours.map((t) => (
            <div key={t.id} data-testid={`sal-archived-${t.id}`} className="w-full rounded-xl border border-white/5 bg-[#12161A] p-4 flex items-center gap-2 opacity-80">
              <button onClick={() => navigate(`/scoreandlive/${t.id}`)} className="flex-1 min-w-0 text-left">
                <span className="font-bold">{t.name}</span>
                <span className="ml-2 text-xs text-[#94A3B8]">Concluso · archiviato</span>
              </button>
              <button data-testid={`sal-unarchive-${t.id}`} onClick={(e) => unarchive(e, t)} title="Ripristina" className="p-2 rounded-md text-[#00D95F] hover:bg-white/5 shrink-0"><ArchiveRestore size={18} /></button>
              <button data-testid={`sal-delete-${t.id}`} onClick={(e) => remove(e, t)} title="Elimina" className="p-2 rounded-md text-[#EF4444] hover:bg-white/5 shrink-0"><Trash2 size={18} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
