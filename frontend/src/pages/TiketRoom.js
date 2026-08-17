import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronLeft, Trophy, Users, Copy, Info } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

export default function TiketRoom() {
  const { roomId } = useParams();
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const [room, setRoom] = useState(null);
  const [board, setBoard] = useState(null);
  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api(`/rooms/${roomId}`);
      setRoom(r);
      setBoard(await api(`/rooms/${roomId}/leaderboard`).catch(() => null));
      setMembers(await api(`/rooms/${roomId}/members`).catch(() => []));
    } catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, [roomId]);
  useEffect(() => { load(); }, [load]);

  if (loading || !room) return <div className="py-16 text-center text-[#94A3B8]">Caricamento...</div>;

  const entries = board?.entries || [];

  return (
    <div className="space-y-5">
      <button data-testid="tkr-back" onClick={() => navigate("/tiket")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Stanze</button>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4">
        <div className="text-2xl font-extrabold">{room.name}</div>
        <div className="flex flex-wrap items-center gap-4 mt-2 text-sm text-[#94A3B8]">
          <span>Giornata <b className="text-white">{room.matchday}</b></span>
          <span>Max eventi: {room.max_events}</span>
          {room.status && <span className="uppercase text-xs">{room.status}</span>}
        </div>
        {room.invite_code && (isAdmin || room.is_admin) && (
          <button data-testid="tkr-copy-code" onClick={() => { navigator.clipboard?.writeText(room.invite_code); toast.success("Codice copiato"); }} className="mt-3 inline-flex items-center gap-2 text-xs bg-white/10 rounded-md px-3 py-1.5">
            <Copy size={13} /> Codice invito: <b>{room.invite_code}</b>
          </button>
        )}
      </div>

      <div className="flex items-start gap-2 p-3 rounded-lg bg-[#242A31] text-[#94A3B8] text-xs">
        <Info size={16} className="mt-0.5 shrink-0" />
        La tua schedina si carica scattando la foto della giocata del bookmaker (OCR). Questa funzione arriverà a breve anche sul web; per ora consulta classifica e partecipanti.
      </div>

      <div>
        <h2 className="text-lg font-extrabold mb-2 flex items-center gap-2"><Trophy size={18} className="text-[#F59E0B]" /> Classifica</h2>
        <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
          {entries.length === 0 ? (
            <div className="px-4 py-8 text-center text-[#94A3B8]">{board?.has_results ? "Nessuna schedina." : "In attesa dei risultati della giornata."}</div>
          ) : (
            entries.map((e, i) => (
              <div key={e.user_id || i} data-testid={`tkr-row-${e.user_id || i}`} className="px-4 py-3 flex items-center gap-3">
                <span className="w-6 text-[#94A3B8] font-bold">{i + 1}</span>
                <span className="flex-1 font-medium">{e.nickname}</span>
                <span className="text-xs text-[#94A3B8]">{e.won_count}/{e.events_count} vinti</span>
                <span className="font-extrabold text-[#F59E0B]">{Number(e.total || 0).toFixed(2)}</span>
              </div>
            ))
          )}
        </div>
      </div>

      <div>
        <h2 className="text-lg font-extrabold mb-2 flex items-center gap-2"><Users size={18} /> Partecipanti ({members.length})</h2>
        <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
          {members.map((m, i) => (
            <div key={m.user_id || i} className="px-4 py-3 flex items-center gap-3">
              <span className="w-6 text-[#94A3B8]">{m.slot ?? i + 1}</span>
              <span className="flex-1">{m.display_name || m.nickname}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
