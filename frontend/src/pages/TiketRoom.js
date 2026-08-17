import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronLeft, Trophy, Users, Copy, Upload, CheckCircle2, Camera } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import InvitesManager from "@/components/InvitesManager";
import NotifyBox from "@/components/NotifyBox";

export default function TiketRoom() {
  const { roomId } = useParams();
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const fileRef = useRef(null);
  const [room, setRoom] = useState(null);
  const [board, setBoard] = useState(null);
  const [members, setMembers] = useState([]);
  const [mySchedina, setMySchedina] = useState(null);
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setRoom(await api(`/rooms/${roomId}`));
      setBoard(await api(`/rooms/${roomId}/leaderboard`).catch(() => null));
      setMembers(await api(`/rooms/${roomId}/members`).catch(() => []));
      setMySchedina(await api(`/rooms/${roomId}/schedina`).catch(() => null));
    } catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, [roomId]);
  useEffect(() => { load(); }, [load]);

  const onFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy(true); setDraft(null);
    try {
      const b64 = await new Promise((res, rej) => {
        const r = new FileReader();
        r.onload = () => res(String(r.result).split(",")[1]);
        r.onerror = rej;
        r.readAsDataURL(f);
      });
      const out = await api(`/rooms/${roomId}/schedina/ocr`, { method: "POST", body: { image_base64: b64 } });
      setDraft(out);
      toast.success(`Trovati ${out.events?.length || 0} eventi. Controlla e conferma.`);
    } catch (e2) { toast.error(e2.message); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };

  const confirm = async () => {
    setBusy(true);
    try {
      await api(`/rooms/${roomId}/schedina/confirm`, { method: "POST", body: {} });
      toast.success("Schedina confermata!");
      setDraft(null);
      load();
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); }
  };

  if (loading || !room) return <div className="py-16 text-center text-[#94A3B8]">Caricamento...</div>;
  const entries = board?.entries || [];
  const canUpload = room.status !== "settled";

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

      {(isAdmin || room.is_admin) && <InvitesManager basePath={`/rooms/${roomId}`} />}
      {(isAdmin || room.is_admin) && <NotifyBox userIds={members.map((m) => m.user_id)} url={`/tiket/${roomId}`} />}

      {/* Schedina upload (OCR) */}
      <div className="rounded-xl border border-[#F59E0B]/40 bg-[#F59E0B]/5 p-4 space-y-3">
        <div className="font-bold flex items-center gap-2"><Camera size={18} className="text-[#F59E0B]" /> La tua schedina</div>        {mySchedina?.events?.length > 0 && !draft && (
          <div className="rounded-lg bg-[#0F1216] border border-white/10 divide-y divide-white/10">
            <div className="px-3 py-2 text-xs text-[#94A3B8] flex items-center gap-1"><CheckCircle2 size={13} className="text-[#00D95F]" /> Confermata · {mySchedina.events.length} eventi</div>
            {mySchedina.events.map((ev, i) => (
              <div key={i} className="px-3 py-2 text-sm flex justify-between gap-2">
                <span className="truncate">{ev.home_team} - {ev.away_team} <b className="text-[#F59E0B]">{ev.prediction}</b></span>
                <span className="text-[#94A3B8]">@ {Number(ev.odd).toFixed(2)}</span>
              </div>
            ))}
          </div>
        )}
        {draft?.events?.length > 0 && (
          <div className="rounded-lg bg-[#0F1216] border border-[#00D95F]/40 divide-y divide-white/10">
            <div className="px-3 py-2 text-xs text-[#00D95F]">Anteprima OCR — verifica prima di confermare</div>
            {draft.events.map((ev, i) => (
              <div key={i} className="px-3 py-2 text-sm flex justify-between gap-2">
                <span className="truncate">{ev.home_team} - {ev.away_team} <b className="text-[#F59E0B]">{ev.prediction}</b></span>
                <span className="text-[#94A3B8]">@ {Number(ev.odd).toFixed(2)}</span>
              </div>
            ))}
            <div className="px-3 py-2">
              <button data-testid="tkr-confirm" onClick={confirm} disabled={busy} className="w-full bg-[#00D95F] text-[#08110A] font-bold rounded-md py-2 disabled:opacity-50">Conferma schedina</button>
            </div>
          </div>
        )}
        {canUpload && (
          <>
            <input ref={fileRef} data-testid="tkr-file" type="file" accept="image/*" onChange={onFile} className="hidden" />
            <button data-testid="tkr-upload" onClick={() => fileRef.current?.click()} disabled={busy} className="w-full flex items-center justify-center gap-2 border border-white/15 rounded-md py-2.5 text-sm hover:bg-white/5 disabled:opacity-50">
              <Upload size={16} /> {busy ? "Analisi in corso..." : mySchedina?.events?.length ? "Carica una nuova schedina" : "Carica foto della schedina"}
            </button>
            <p className="text-xs text-[#94A3B8]">Scatta o carica lo screenshot della giocata del bookmaker: la leggiamo automaticamente (OCR).</p>
          </>
        )}
      </div>

      <div>
        <h2 className="text-lg font-extrabold mb-2 flex items-center gap-2"><Trophy size={18} className="text-[#F59E0B]" /> Classifica</h2>
        <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
          {entries.length === 0 ? (
            <div className="px-4 py-8 text-center text-[#94A3B8]">{board?.has_results ? "Nessuna schedina." : "In attesa dei risultati della giornata."}</div>
          ) : entries.map((e, i) => (
            <div key={e.user_id || i} data-testid={`tkr-row-${e.user_id || i}`} className="px-4 py-3 flex items-center gap-3">
              <span className="w-6 text-[#94A3B8] font-bold">{i + 1}</span>
              <span className="flex-1 font-medium">{e.nickname}</span>
              <span className="text-xs text-[#94A3B8]">{e.won_count}/{e.events_count} vinti</span>
              <span className="font-extrabold text-[#F59E0B]">{Number(e.total || 0).toFixed(2)}</span>
            </div>
          ))}
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
