import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Plus, ChevronLeft, Users, ChevronRight, Trash2, Archive, ArchiveRestore } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { BonusBanner } from "@/components/BonusBanner";

export default function Tiket() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const [rooms, setRooms] = useState([]);
  const [loading, setLoading] = useState(true);
  const [code, setCode] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [matchday, setMatchday] = useState(1);
  const [maxEvents, setMaxEvents] = useState(5);
  const [showArchived, setShowArchived] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try { setRooms(await api("/rooms?game=thebesttiket")); } catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const join = async () => {
    if (!code.trim()) return;
    try {
      const r = await api("/rooms/join", { method: "POST", body: { invite_code: code.trim() } });
      toast.success("Entrato nella stanza!");
      setCode("");
      navigate(`/tiket/${r.id}`);
    } catch (e) { toast.error(e.message); }
  };

  const create = async () => {
    try {
      const r = await api("/rooms", { method: "POST", body: { name, matchday: Number(matchday), max_events: Number(maxEvents), game: "thebesttiket" } });
      toast.success("Stanza creata!");
      setCreating(false); setName("");
      navigate(`/tiket/${r.id}`);
    } catch (e) { toast.error(e.message); }
  };

  const remove = async (e, r) => {
    e.stopPropagation();
    if (!window.confirm(`Eliminare la stanza "${r.name}"? Verranno rimossi iscritti, schedine e risultati. Operazione irreversibile.`)) return;
    try { await api(`/rooms/${r.id}`, { method: "DELETE" }); toast.success("Stanza eliminata"); load(); }
    catch (err) { toast.error(err.message); }
  };

  const archive = async (e, r) => {
    e.stopPropagation();
    if (!window.confirm(`Archiviare la stanza "${r.name}"? Resterà consultabile nella sezione Archiviate.`)) return;
    try { await api(`/rooms/${r.id}/archive?archived=true`, { method: "POST" }); toast.success("Stanza archiviata"); load(); }
    catch (err) { toast.error(err.message); }
  };
  const unarchive = async (e, r) => {
    e.stopPropagation();
    try { await api(`/rooms/${r.id}/archive?archived=false`, { method: "POST" }); toast.success("Stanza ripristinata"); load(); }
    catch (err) { toast.error(err.message); }
  };

  const activeRooms = rooms.filter((r) => !r.archived);
  const archivedRooms = rooms.filter((r) => r.archived);

  return (
    <div className="space-y-5">
      <button data-testid="tiket-back" onClick={() => navigate("/")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Hub</button>

      <BonusBanner game="tiket" />

      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-extrabold text-[#F59E0B]">TheBestTiket</h1>
          <p className="text-[#94A3B8] text-sm">Schedine Serie A tra amici. Chi ha la quota più bassa, paga da bere.</p>
        </div>
        {isAdmin && (
          <button data-testid="tiket-new" onClick={() => setCreating((v) => !v)} className="text-sm bg-white/10 hover:bg-white/20 border border-white/15 rounded-md px-3 py-2 flex items-center gap-1.5 transition-colors">
            <Plus size={16} /> Nuova
          </button>
        )}
      </div>

      {isAdmin && creating && (
        <div className="rounded-xl border border-[#F59E0B]/40 bg-[#F59E0B]/5 p-4 space-y-3">
          <input data-testid="tk-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome stanza" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-[#F59E0B]" />
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-[#94A3B8]">Giornata
              <input data-testid="tk-md" type="number" min="1" max="38" value={matchday} onChange={(e) => setMatchday(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
            </label>
            <label className="text-xs text-[#94A3B8]">Max eventi
              <input data-testid="tk-maxev" type="number" min="1" max="20" value={maxEvents} onChange={(e) => setMaxEvents(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
            </label>
          </div>
          <button data-testid="tk-create" onClick={create} disabled={!name} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Crea stanza</button>
        </div>
      )}

      <div className="flex gap-2">
        <input data-testid="tk-join-code" value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="Codice invito" className="flex-1 bg-[#181D22] border border-white/15 rounded-md px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-[#F59E0B]" />
        <button data-testid="tk-join" onClick={join} className="bg-[#00D95F] text-[#08110A] font-bold rounded-md px-5">Entra</button>
      </div>

      {loading ? (
        <div className="py-12 text-center text-[#94A3B8]">Caricamento...</div>
      ) : activeRooms.length === 0 ? (
        <div className="rounded-xl border border-white/10 bg-[#181D22] p-8 text-center text-[#94A3B8]">Nessuna stanza. Usa un codice invito per entrare.</div>
      ) : (
        <div className="space-y-3">
          {activeRooms.map((r) => (
            <div key={r.id} data-testid={`tk-card-${r.id}`} className="w-full rounded-xl border border-white/10 bg-[#181D22] p-4 hover:border-[#F59E0B]/60 transition-colors flex items-center gap-3">
              <button onClick={() => navigate(`/tiket/${r.id}`)} className="flex-1 min-w-0 text-left">
                <div className="text-lg font-extrabold">{r.name}</div>
                <div className="flex items-center gap-3 mt-1 text-sm text-[#94A3B8]">
                  <span>Giornata {r.matchday}</span>
                  {r.members_count != null && <span className="flex items-center gap-1"><Users size={13} /> {r.members_count}</span>}
                  {r.status && <span className="uppercase text-xs">{r.status}</span>}
                </div>
              </button>
              {isAdmin && r.status === "settled" && (
                <button data-testid={`tk-archive-${r.id}`} onClick={(e) => archive(e, r)} title="Archivia stanza" className="p-2 rounded-md text-[#F59E0B] hover:bg-[#F59E0B]/10 shrink-0"><Archive size={18} /></button>
              )}
              {isAdmin && (
                <button data-testid={`tk-delete-${r.id}`} onClick={(e) => remove(e, r)} title="Elimina stanza" className="p-2 rounded-md text-[#EF4444] hover:bg-[#EF4444]/10 shrink-0"><Trash2 size={18} /></button>
              )}
              <ChevronRight className="text-[#F59E0B] shrink-0" size={20} />
            </div>
          ))}
        </div>
      )}

      {isAdmin && archivedRooms.length > 0 && (
        <div className="space-y-3">
          <button data-testid="tk-archived-toggle" onClick={() => setShowArchived((v) => !v)} className="flex items-center gap-2 text-sm text-[#94A3B8] hover:text-white transition-colors">
            <Archive size={15} /> Archiviate ({archivedRooms.length}) {showArchived ? "▲" : "▼"}
          </button>
          {showArchived && archivedRooms.map((r) => (
            <div key={r.id} data-testid={`tk-archived-${r.id}`} className="w-full rounded-xl border border-white/5 bg-[#12161A] p-4 flex items-center gap-2 opacity-80">
              <button onClick={() => navigate(`/tiket/${r.id}`)} className="flex-1 min-w-0 text-left">
                <span className="font-bold">{r.name}</span>
                <span className="ml-2 text-xs text-[#94A3B8]">G{r.matchday} · archiviata</span>
              </button>
              <button data-testid={`tk-unarchive-${r.id}`} onClick={(e) => unarchive(e, r)} title="Ripristina" className="p-2 rounded-md text-[#00D95F] hover:bg-white/5 shrink-0"><ArchiveRestore size={18} /></button>
              <button data-testid={`tk-delete-${r.id}`} onClick={(e) => remove(e, r)} title="Elimina" className="p-2 rounded-md text-[#EF4444] hover:bg-white/5 shrink-0"><Trash2 size={18} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
