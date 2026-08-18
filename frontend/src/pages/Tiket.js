import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Plus, ChevronLeft, Users, ChevronRight } from "lucide-react";
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
      ) : rooms.length === 0 ? (
        <div className="rounded-xl border border-white/10 bg-[#181D22] p-8 text-center text-[#94A3B8]">Nessuna stanza. Usa un codice invito per entrare.</div>
      ) : (
        <div className="space-y-3">
          {rooms.map((r) => (
            <button key={r.id} data-testid={`tk-card-${r.id}`} onClick={() => navigate(`/tiket/${r.id}`)} className="w-full text-left rounded-xl border border-white/10 bg-[#181D22] p-4 hover:border-[#F59E0B]/60 transition-colors flex items-center gap-3">
              <div className="flex-1">
                <div className="text-lg font-extrabold">{r.name}</div>
                <div className="flex items-center gap-3 mt-1 text-sm text-[#94A3B8]">
                  <span>Giornata {r.matchday}</span>
                  {r.members_count != null && <span className="flex items-center gap-1"><Users size={13} /> {r.members_count}</span>}
                  {r.status && <span className="uppercase text-xs">{r.status}</span>}
                </div>
              </div>
              <ChevronRight className="text-[#F59E0B]" size={20} />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
