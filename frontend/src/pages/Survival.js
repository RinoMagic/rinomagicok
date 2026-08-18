import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Heart, Plus, ChevronLeft, Ticket, Trash2, Archive, ArchiveRestore } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { BonusBanner } from "@/components/BonusBanner";

export default function Survival() {
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
    try { setTours(await api("/sv/tournaments?include_finished=true")); } catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const join = async () => {
    if (!code.trim()) return;
    try {
      const t = await api("/sv/tournaments/join", { method: "POST", body: { invite_code: code.trim() } });
      toast.success("Iscritto al torneo!");
      setCode("");
      navigate(`/survival/${t.id}`);
    } catch (e) { toast.error(e.message); }
  };

  const create = async () => {
    try {
      const t = await api("/sv/tournaments", { method: "POST", body: { name, initial_lives: Number(lives), start_matchday: Number(startMd) } });
      toast.success("Torneo creato!");
      setCreating(false); setName("");
      navigate(`/survival/${t.id}`);
    } catch (e) { toast.error(e.message); }
  };

  const remove = async (e, t) => {
    e.stopPropagation();
    if (!window.confirm(`Eliminare il torneo "${t.name}"? Verranno rimossi iscritti, giornate e pronostici. Operazione irreversibile.`)) return;
    try { await api(`/sv/tournaments/${t.id}`, { method: "DELETE" }); toast.success("Torneo eliminato"); load(); }
    catch (err) { toast.error(err.message); }
  };

  const archive = async (e, t) => {
    e.stopPropagation();
    if (!window.confirm(`Archiviare il torneo "${t.name}"? Resterà consultabile nella sezione Archiviati.`)) return;
    try { await api(`/sv/tournaments/${t.id}/archive?archived=true`, { method: "POST" }); toast.success("Torneo archiviato"); load(); }
    catch (err) { toast.error(err.message); }
  };

  const unarchive = async (e, t) => {
    e.stopPropagation();
    try { await api(`/sv/tournaments/${t.id}/archive?archived=false`, { method: "POST" }); toast.success("Torneo ripristinato"); load(); }
    catch (err) { toast.error(err.message); }
  };

  const activeTours = tours.filter((t) => !t.archived);
  const archivedTours = tours.filter((t) => t.archived);

  return (
    <div className="space-y-5">
      <button data-testid="survival-back" onClick={() => navigate("/")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Hub</button>

      <BonusBanner game="survival" />

      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-extrabold text-[#EF4444]">Survival 2.0</h1>
          <p className="text-[#94A3B8] text-sm">3 vite, 1 pronostico a vita per giornata: sopravvivi!</p>
        </div>
        {isAdmin && (
          <button data-testid="survival-new" onClick={() => setCreating((v) => !v)} className="text-sm bg-white/10 hover:bg-white/20 border border-white/15 rounded-md px-3 py-2 flex items-center gap-1.5 transition-colors">
            <Plus size={16} /> Nuovo
          </button>
        )}
      </div>

      {isAdmin && creating && (
        <div className="rounded-xl border border-[#EF4444]/40 bg-[#EF4444]/5 p-4 space-y-3">
          <div className="grid sm:grid-cols-3 gap-3">
            <input data-testid="sv-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome torneo" className="sm:col-span-3 bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[#F59E0B]" />
            <label className="text-xs text-[#94A3B8]">Vite iniziali
              <input data-testid="sv-lives" type="number" min="1" max="10" value={lives} onChange={(e) => setLives(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
            </label>
            <label className="text-xs text-[#94A3B8]">Giornata iniziale
              <input data-testid="sv-startmd" type="number" min="1" max="38" value={startMd} onChange={(e) => setStartMd(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
            </label>
          </div>
          <button data-testid="sv-create" onClick={create} disabled={!name} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Crea torneo</button>
        </div>
      )}

      <div className="flex gap-2">
        <input data-testid="sv-join-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="Codice invito" className="flex-1 bg-[#181D22] border border-white/15 rounded-md px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-[#F59E0B]" />
        <button data-testid="sv-join" onClick={join} className="bg-[#00D95F] text-[#08110A] font-bold rounded-md px-5 transition-colors">Entra</button>
      </div>

      {loading ? (
        <div className="py-12 text-center text-[#94A3B8]">Caricamento...</div>
      ) : activeTours.length === 0 ? (
        <div className="rounded-xl border border-white/10 bg-[#181D22] p-8 text-center text-[#94A3B8]">Nessun torneo attivo. Usa un codice invito per entrare.</div>
      ) : (
        <div className="space-y-3">
          {activeTours.map((t) => (
            <div key={t.id} data-testid={`sv-card-${t.id}`} className="w-full rounded-xl border border-white/10 bg-[#181D22] p-4 flex items-start gap-2 hover:border-[#EF4444]/60 transition-colors">
              <button onClick={() => navigate(`/survival/${t.id}`)} className="flex-1 min-w-0 text-left">
                <div className="flex items-center justify-between">
                  <span className="text-lg font-extrabold">{t.name}</span>
                  <span className="text-xs px-2 py-1 rounded-full bg-white/10 text-[#94A3B8]">G{t.current_matchday}</span>
                </div>
                <div className="flex items-center gap-4 mt-2 text-sm text-[#94A3B8]">
                  <span className="flex items-center gap-1 text-[#EF4444]"><Heart size={14} /> {t.players_alive}/{t.players_total} vivi</span>
                  <span>{t.joined ? "Iscritto" : "Non iscritto"}</span>
                  {t.status === "finished" && <span className="text-[#F59E0B]">Concluso</span>}
                </div>
              </button>
              {isAdmin && t.status === "finished" && (
                <button data-testid={`sv-archive-${t.id}`} onClick={(e) => archive(e, t)} title="Archivia torneo" className="p-2 rounded-md text-[#F59E0B] hover:bg-[#F59E0B]/10 shrink-0"><Archive size={18} /></button>
              )}
              {isAdmin && (
                <button data-testid={`sv-delete-${t.id}`} onClick={(e) => remove(e, t)} title="Elimina torneo" className="p-2 rounded-md text-[#EF4444] hover:bg-[#EF4444]/10 shrink-0"><Trash2 size={18} /></button>
              )}
            </div>
          ))}
        </div>
      )}

      {isAdmin && archivedTours.length > 0 && (
        <div className="space-y-3">
          <button data-testid="sv-archived-toggle" onClick={() => setShowArchived((v) => !v)} className="flex items-center gap-2 text-sm text-[#94A3B8] hover:text-white transition-colors">
            <Archive size={15} /> Archiviati ({archivedTours.length}) {showArchived ? "▲" : "▼"}
          </button>
          {showArchived && archivedTours.map((t) => (
            <div key={t.id} data-testid={`sv-archived-${t.id}`} className="w-full rounded-xl border border-white/5 bg-[#12161A] p-4 flex items-center gap-2 opacity-80">
              <button onClick={() => navigate(`/survival/${t.id}`)} className="flex-1 min-w-0 text-left">
                <span className="font-bold">{t.name}</span>
                <span className="ml-2 text-xs text-[#94A3B8]">Concluso · archiviato</span>
              </button>
              <button data-testid={`sv-unarchive-${t.id}`} onClick={(e) => unarchive(e, t)} title="Ripristina" className="p-2 rounded-md text-[#00D95F] hover:bg-white/5 shrink-0"><ArchiveRestore size={18} /></button>
              <button data-testid={`sv-delete-${t.id}`} onClick={(e) => remove(e, t)} title="Elimina" className="p-2 rounded-md text-[#EF4444] hover:bg-white/5 shrink-0"><Trash2 size={18} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
