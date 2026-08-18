import { useEffect, useState } from "react";
import { X, Gift, EyeOff, CheckCircle2, Clock, HeartCrack } from "lucide-react";
import { api } from "@/lib/api";

// Dettaglio giocate di un partecipante Survival, raggruppate per giornata.
// Replica del modal originale (leaderboard-click → picks per giornata).
export function SurvivaPicksModal({ tid, row, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!row) return;
    let alive = true;
    setData(null); setErr(null);
    api(`/sv/tournaments/${tid}/participants/${row.user_id}/picks`)
      .then((r) => { if (alive) setData(r); })
      .catch((e) => { if (alive) setErr(e.message || "Errore"); });
    return () => { alive = false; };
  }, [tid, row]);

  if (!row) return null;

  const lives = row.lives_left;
  const sub = `#${row.rank} · ${lives} ${lives === 1 ? "vita" : "vite"}${row.eliminated ? (row.eliminated_matchday ? ` · Eliminato G${row.eliminated_matchday}` : " · Eliminato") : ""}`;

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/60 p-0 sm:p-4" onClick={onClose} data-testid="sv-picks-modal">
      <div className="w-full sm:max-w-lg bg-[#12161A] rounded-t-2xl sm:rounded-2xl border border-white/10 max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start gap-3 p-4 border-b border-white/10">
          <div className="flex-1 min-w-0">
            <div className="text-lg font-extrabold truncate">{row.nickname}</div>
            <div className="text-xs text-[#94A3B8] mt-0.5">{sub}</div>
          </div>
          <button onClick={onClose} data-testid="sv-modal-close" className="p-1 text-[#94A3B8] hover:text-white"><X size={22} /></button>
        </div>

        <div className="overflow-y-auto p-4 space-y-2">
          {err && <div className="rounded-md border border-[#EF4444]/40 bg-[#EF4444]/10 p-3 text-sm text-[#EF4444]">{err}</div>}
          {!data && !err && <div className="py-8 text-center text-[#94A3B8]">Caricamento...</div>}
          {data && data.matchdays.length === 0 && <div className="text-sm text-[#64748B] italic">Nessuna giornata giocata.</div>}
          {(() => {
            if (!data) return null;
            const rel = data.matchdays.filter((m) => m.settled || m.deadline_passed || (m.picks && m.picks.length > 0));
            if (rel.length === 0) return <div className="text-sm text-[#64748B] italic">Nessuna giocata ancora visibile.</div>;
            return rel.map((md) => (
            <div key={md.matchday_id} data-testid={`sv-modal-md-${md.matchday}`} className="rounded-xl border border-white/10 bg-[#181D22] p-3 space-y-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="font-extrabold text-sm">Giornata {md.matchday}</span>
                  {md.big_match_bonus_won && (
                    <span title="Big Match Bonus: +1 vita" className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-[#F59E0B]/15 border border-[#F59E0B]"><Gift size={12} className="text-[#F59E0B]" /></span>
                  )}
                </div>
                <span className={`text-[10px] font-extrabold px-2 py-0.5 rounded ${md.settled ? "bg-[#00D95F]/20 text-[#00D95F]" : "bg-white/10 text-[#94A3B8]"}`}>
                  {md.settled ? "Calcolata" : md.deadline_passed ? "Chiusa" : "Aperta"}
                </span>
              </div>

              {md.hidden ? (
                <div className="flex items-center gap-2 rounded-lg bg-[#0F1216] border border-white/10 p-2.5 text-xs text-[#94A3B8] italic">
                  <EyeOff size={14} /> Pronostici nascosti finché il timer non scade
                </div>
              ) : (md.picks && md.picks.length > 0) ? (
                <div className="space-y-1">
                  {md.picks.map((p, i) => {
                    // Suspended match (settled matchday, no result) stays VALID
                    // and green — unlike postponed. Only correct===false is red.
                    const outcome = md.settled ? (p.correct === false ? "ko" : "ok") : "na";
                    const suspended = md.settled && (p.correct === null || p.correct === undefined);
                    return (
                      <div key={i} className="flex items-center gap-2 py-1">
                        <span className="flex-1 min-w-0 truncate text-sm flex items-center gap-1.5">
                          {p.home_team} <span className="text-[#94A3B8]">-</span> {p.away_team}
                          {p.auto_generated && <span title="Giocata di default assegnata automaticamente" className="text-[9px] px-1.5 py-0.5 rounded bg-white/10 text-[#94A3B8] font-bold shrink-0">AUTO</span>}
                          {suspended && <span title="Partita sospesa: giocata valida" className="text-[9px] px-1.5 py-0.5 rounded bg-[#00D95F]/15 text-[#00D95F] border border-[#00D95F]/40 font-bold shrink-0">SOSPESA</span>}
                        </span>
                        <span className={`min-w-7 text-center px-2 py-0.5 rounded border text-sm font-extrabold ${outcome === "ok" ? "border-[#00D95F] bg-[#00D95F]/15 text-[#00D95F]" : outcome === "ko" ? "border-[#EF4444] bg-[#EF4444]/15 text-[#EF4444]" : "border-white/15 text-white"}`}>{p.pick}</span>
                        <span className="w-6 flex items-center justify-center">
                          {outcome === "ok" && <CheckCircle2 size={18} className="text-[#00D95F]" />}
                          {outcome === "ko" && <HeartCrack size={18} className="text-[#EF4444]" />}
                          {outcome === "na" && <Clock size={15} className="text-[#94A3B8]" />}
                        </span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-sm text-[#64748B] italic">Nessuna scelta inviata.</div>
              )}
            </div>
            ));
          })()}
        </div>
      </div>
    </div>
  );
}
