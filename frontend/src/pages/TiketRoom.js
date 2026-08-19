import { useEffect, useState, useCallback, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronLeft, Trophy, Users, Copy, Upload, CheckCircle2, Camera, ChevronDown, Beer, Clock } from "lucide-react";
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
  const [openRow, setOpenRow] = useState(null);

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
  const entries = board?.leaderboard || [];
  const hasResults = !!board?.has_results;
  const canUpload = room.status !== "settled";

  return (
    <div className="space-y-5">
      <button data-testid="tkr-back" onClick={() => navigate("/tiket")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Stanze</button>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4">
        <div className="text-2xl font-extrabold">{room.name}</div>
        <div className="flex flex-wrap items-center gap-4 mt-2 text-sm text-[#94A3B8]">
          <span>Giornata <b className="text-white">{room.matchday}</b></span>
          <span>Max eventi: {room.max_events}</span>
          {room.status && <span data-testid="tkr-room-status" className={`text-xs font-bold uppercase px-2 py-0.5 rounded-full ${room.status === "settled" ? "bg-[#00D95F]/20 text-[#00D95F]" : "bg-white/10 text-[#94A3B8]"}`}>{room.status === "settled" ? "Concluso" : room.status === "closed" ? "Chiusa" : "Aperta"}</span>}
        </div>
        {room.invite_code && (isAdmin || room.is_admin) && (
          <button data-testid="tkr-copy-code" onClick={() => { navigator.clipboard?.writeText(room.invite_code); toast.success("Codice copiato"); }} className="mt-3 inline-flex items-center gap-2 text-xs bg-white/10 rounded-md px-3 py-1.5">
            <Copy size={13} /> Codice invito: <b>{room.invite_code}</b>
          </button>
        )}
      </div>

      {/* Partecipanti + stato consegna schedina */}
      <div>
        <h2 className="text-lg font-extrabold mb-2 flex items-center gap-2"><Users size={18} className="text-[#F59E0B]" /> Partecipanti ({members.length})</h2>
        <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
          {members.length === 0 && <div className="px-4 py-6 text-center text-[#94A3B8]">Nessun partecipante.</div>}
          {members.map((m) => (
            <div key={m.user_id} data-testid={`tkr-member-${m.user_id}`} className="px-4 py-2.5 flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-[#F59E0B]/20 flex items-center justify-center text-xs font-bold text-[#F59E0B] shrink-0">{(m.nickname || "?").slice(0, 2).toUpperCase()}</div>
              <span className="flex-1 min-w-0 truncate font-medium">{m.nickname}</span>
              {m.submitted ? (
                <span data-testid={`tkr-badge-ok-${m.user_id}`} className="inline-flex items-center gap-1 text-xs font-bold px-2 py-1 rounded-full bg-[#00D95F]/15 text-[#00D95F] border border-[#00D95F]/40"><CheckCircle2 size={13} /> Giocata effettuata</span>
              ) : (
                <span data-testid={`tkr-badge-pending-${m.user_id}`} className="inline-flex items-center gap-1 text-xs font-bold px-2 py-1 rounded-full bg-white/5 text-[#94A3B8] border border-white/10"><Clock size={13} /> In attesa</span>
              )}
            </div>
          ))}
        </div>
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
        <p className="text-xs text-[#94A3B8] mb-2">Tocca un giocatore per vedere la sua schedina.</p>
        <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
          {entries.length === 0 ? (
            <div className="px-4 py-8 text-center text-[#94A3B8]">{hasResults ? "Nessuna schedina consegnata." : "Risultati non ancora inseriti: l'admin deve caricare i risultati della giornata."}</div>
          ) : entries.map((e, i) => {
            const isLast = i === entries.length - 1;
            const open = openRow === (e.user_id || i);
            return (
              <div key={e.user_id || i} data-testid={`tkr-row-${e.user_id || i}`}>
                <button onClick={() => setOpenRow(open ? null : (e.user_id || i))} className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-white/5 transition-colors">
                  <span className="w-6 text-[#94A3B8] font-bold">{e.rank || i + 1}</span>
                  <div className="w-8 h-8 rounded-full bg-[#F59E0B]/20 flex items-center justify-center text-xs font-bold text-[#F59E0B] shrink-0">{(e.nickname || "?").slice(0, 2).toUpperCase()}</div>
                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate">{e.nickname}</div>
                    <div className="text-xs text-[#94A3B8]">{e.won_count}/{e.events_count} azzeccate</div>
                  </div>
                  {i === 0 && Number(e.total) > 0 && <Trophy size={16} className="text-[#F59E0B]" />}
                  {isLast && entries.length > 1 && <Beer size={16} className="text-[#F59E0B]" />}
                  <span className="font-extrabold text-[#F59E0B]">{Number(e.total || 0).toFixed(2)}</span>
                  <ChevronDown size={16} className={`text-[#94A3B8] transition-transform ${open ? "rotate-180" : ""}`} />
                </button>
                {open && (
                  <div data-testid={`tkr-breakdown-${e.user_id || i}`} className="px-4 pb-3 space-y-1.5">
                    {(e.breakdown || []).length === 0 && <div className="text-xs text-[#64748B]">Nessun evento.</div>}
                    {(e.breakdown || []).map((b, j) => {
                      const evaluated = hasResults && b.matched_fixture;
                      const isVoid = !!b.void;                       // stake returned, quota 1.00
                      const isWin = b.won && !isVoid;                // real win (odd counts)
                      const isLose = evaluated && !b.won && !isVoid;  // lost
                      const cls = isVoid ? "border-white/10 bg-[#0F1216]" : isWin ? "border-[#00D95F]/40 bg-[#00D95F]/10" : isLose ? "border-[#EF4444]/40 bg-[#EF4444]/10" : "border-white/10 bg-[#0F1216]";
                      const voidLabel = b.suspended ? "NULLA" : b.postponed && b.score === "ESCL." ? "ESCL." : "RINV.";
                      return (
                        <div key={j} data-testid={`tkr-event-${e.user_id || i}-${j}`} className={`rounded-lg border p-2.5 text-sm flex items-center gap-2 ${cls}`}>
                          <div className="flex-1 min-w-0">
                            <div className="truncate flex items-center gap-1.5">{b.home_team} <span className="text-[#94A3B8]">-</span> {b.away_team}
                              {b.suspended && <span className="text-[9px] px-1.5 py-0.5 rounded bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/40 font-bold shrink-0">SOSPESA</span>}
                            </div>
                            <div className="text-xs text-[#94A3B8]">Pronostico <b className="text-[#F59E0B]">{b.prediction}</b> @ {isVoid ? "1.00" : Number(b.odd).toFixed(2)}{isVoid && <span className="text-[#64748B]"> (orig. {Number(b.odd).toFixed(2)})</span>}</div>
                          </div>
                          {b.score && <span className="text-xs text-[#94A3B8]">{b.score}</span>}
                          {isVoid ? <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/10 text-[#94A3B8] font-bold">{voidLabel}</span>
                            : isWin ? <CheckCircle2 size={16} className="text-[#00D95F]" />
                            : isLose ? <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#EF4444]/25 text-[#EF4444] font-bold">PERSA</span>
                            : <span className="text-[10px] text-[#64748B]">—</span>}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
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
