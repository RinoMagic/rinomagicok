import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Heart, ChevronLeft, Skull, Lock, Send, Trophy, Copy, BarChart3, FileDown, Gift, ChevronRight, CheckCircle2, Circle } from "lucide-react";
import { api, apiDownload } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import InvitesManager from "@/components/InvitesManager";
import NotifyBox from "@/components/NotifyBox";
import { SurvivaPicksModal } from "@/components/SurvivaPicksModal";

const SIGNS = ["1", "X", "2"];

export default function SurvivalDetail() {
  const { tid } = useParams();
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const [t, setT] = useState(null);
  const [md, setMd] = useState(null);
  const [participants, setParticipants] = useState([]);
  const [locked, setLocked] = useState({ locked_teams: [], lives_left: 0 });
  const [myPicks, setMyPicks] = useState({ picks: [], required: 0 });
  const [sel, setSel] = useState({}); // fixture_key -> sign
  const [summary, setSummary] = useState(null);
  const [mdList, setMdList] = useState([]);
  const [summaryMd, setSummaryMd] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedRow, setSelectedRow] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const tour = await api(`/sv/tournaments/${tid}`);
      setT(tour);
      const parts = await api(`/sv/tournaments/${tid}/leaderboard`);
      setParticipants(parts);
      if (tour.joined) {
        const lk = await api(`/sv/tournaments/${tid}/locked-teams`).catch(() => ({ locked_teams: [], lives_left: 0 }));
        setLocked(lk);
      }
      try {
        const cur = await api(`/sv/tournaments/${tid}/matchdays/current`);
        setMd(cur);
        if (tour.joined) {
          const mp = await api(`/sv/tournaments/${tid}/matchdays/${cur.id}/my-picks`).catch(() => ({ picks: [], required: 0 }));
          setMyPicks(mp);
          const pre = {};
          (mp.picks || []).forEach((p) => { pre[`${p.home_team}||${p.away_team}`] = p.pick; });
          setSel(pre);
        }
      } catch { setMd(null); }
    } catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, [tid]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    api(`/sv/tournaments/${tid}/matchdays`).then((r) => setMdList(Array.isArray(r) ? r : (r.matchdays || []))).catch(() => {});
  }, [tid]);

  const required = myPicks.required || locked.lives_left || 0;
  const selCount = Object.keys(sel).length;

  const toggle = (home, away, sign) => {
    const key = `${home}||${away}`;
    setSel((prev) => {
      const next = { ...prev };
      if (next[key] === sign) { delete next[key]; return next; }
      if (!next[key] && selCount >= required) {
        toast.warning(`Puoi selezionare solo ${required} partite (1 per vita).`);
        return prev;
      }
      next[key] = sign;
      return next;
    });
  };

  const submit = async () => {
    const picks = Object.entries(sel).map(([k, pick]) => {
      const [home_team, away_team] = k.split("||");
      return { home_team, away_team, pick };
    });
    if (picks.length !== required) { toast.error(`Devi inviare esattamente ${required} pronostici.`); return; }
    try {
      await api(`/sv/tournaments/${tid}/matchdays/${md.id}/picks`, { method: "POST", body: { picks } });
      toast.success("Pronostici inviati!");
      load();
    } catch (e) { toast.error(e.message); }
  };

  const join = async () => {
    if (!t?.invite_code) return;
    try { await api("/sv/tournaments/join", { method: "POST", body: { invite_code: t.invite_code } }); toast.success("Iscritto!"); load(); }
    catch (e) { toast.error(e.message); }
  };

  const loadSummary = async (mdId) => {
    const id = mdId || md?.id;
    if (!id) { toast.info("Nessuna giornata disponibile"); return; }
    setSummaryMd(id);
    try { setSummary(await api(`/sv/tournaments/${tid}/matchdays/${id}/summary`)); }
    catch (e) { toast.error(e.message); }
  };

  const exportPdf = async () => {
    const sections = [{
      heading: "Classifica",
      columns: ["#", "Giocatore", "Vite", "Stato"],
      rows: participants.map((p, i) => [i + 1, p.nickname, p.lives_left, p.eliminated_at != null ? `Fuori G${p.eliminated_at}` : "In gioco"]),
    }];
    if (summary?.fixtures) {
      sections.push({
        heading: `Riepilogo giornata`,
        columns: ["Partita", "1", "X", "2"],
        rows: summary.fixtures.map((f) => [`${f.home_team} - ${f.away_team}`, f.counts?.["1"] ?? 0, f.counts?.["X"] ?? 0, f.counts?.["2"] ?? 0]),
      });
    }
    try {
      await apiDownload("/export/pdf", { title: `Survival · ${t.name}`, subtitle: `Giornata ${md?.matchday ?? t.current_matchday}`, filename: `survival_${(t.name || "torneo").replace(/\s+/g, "_")}`, sections }, "survival.pdf");
    } catch (e) { toast.error(e.message); }
  };

  if (loading || !t) return <div className="py-16 text-center text-[#94A3B8]">Caricamento...</div>;

  const alive = !myPicks && false;
  const lockedSet = new Set(locked.locked_teams || []);

  return (
    <div className="space-y-5">
      <button data-testid="svd-back" onClick={() => navigate("/survival")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Tornei</button>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4">
        <div className="text-2xl font-extrabold">{t.name}</div>
        <div className="flex flex-wrap items-center gap-4 mt-2 text-sm text-[#94A3B8]">
          <span>Giornata <b className="text-white">{t.current_matchday}</b></span>
          <span className="text-[#EF4444] flex items-center gap-1"><Heart size={14} /> {t.players_alive}/{t.players_total} vivi</span>
          {t.joined && <span className="text-[#F59E0B] flex items-center gap-1"><Heart size={14} /> Le tue vite: {locked.lives_left}</span>}
          {t.status === "finished" && <span className="text-[#F59E0B] font-bold">Concluso</span>}
        </div>
        {(isAdmin || t.is_admin) && t.invite_code && (
          <button data-testid="svd-copy-code" onClick={() => { navigator.clipboard?.writeText(t.invite_code); toast.success("Codice copiato"); }} className="mt-3 inline-flex items-center gap-2 text-xs bg-white/10 rounded-md px-3 py-1.5">
            <Copy size={13} /> Codice invito: <b>{t.invite_code}</b>
          </button>
        )}
        {!t.joined && t.status !== "finished" && (
          <button data-testid="svd-join" onClick={join} className="mt-3 w-full bg-[#00D95F] text-[#08110A] font-bold rounded-md py-2.5">Iscriviti a questo torneo</button>
        )}
      </div>

      {t.joined && locked.locked_teams?.length > 0 && (
        <div className="rounded-lg bg-[#242A31] p-3 text-sm">
          <div className="text-xs uppercase tracking-widest text-[#94A3B8] mb-1 flex items-center gap-1"><Lock size={12} /> Squadre bloccate</div>
          <div className="flex flex-wrap gap-1.5">
            {locked.locked_teams.map((tm) => <span key={tm} className="px-2 py-0.5 rounded bg-white/10 text-xs">{tm}</span>)}
          </div>
        </div>
      )}

      {t.joined && md && !md.settled && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-lg font-extrabold">Pronostici · G{md.matchday}</h2>
            <span className={`text-sm font-bold ${selCount === required ? "text-[#00D95F]" : "text-[#F59E0B]"}`}>{selCount}/{required} scelti</span>
          </div>
          {md.locked ? (
            <div className="rounded-lg bg-[#242A31] p-4 text-center text-[#94A3B8] flex items-center justify-center gap-2"><Lock size={16} /> Giornata chiusa: pronostici bloccati.</div>
          ) : required === 0 ? (
            <div className="rounded-lg bg-[#EF4444]/10 border border-[#EF4444]/40 p-4 text-center text-[#EF4444] flex items-center justify-center gap-2"><Skull size={16} /> Sei stato eliminato.</div>
          ) : (
            <>
              <p className="text-xs text-[#94A3B8] mb-2">Scegli {required} partite diverse (una per vita) e il segno 1/X/2.</p>
              <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
                {(md.fixtures || []).map((f, i) => {
                  const key = `${f.home_team}||${f.away_team}`;
                  const cur = sel[key];
                  const postponed = f.postponed_before;
                  return (
                    <div key={i} data-testid={`svd-fixture-${i}`} className={`px-3 py-3 ${postponed ? "opacity-40" : ""}`}>
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-sm flex-1 min-w-0">
                          <span className={lockedSet.has(f.home_team) ? "text-[#F59E0B]" : ""}>{f.home_team}</span>
                          <span className="text-[#94A3B8] mx-1">-</span>
                          <span className={lockedSet.has(f.away_team) ? "text-[#F59E0B]" : ""}>{f.away_team}</span>
                        </div>
                        <div className="flex gap-1">
                          {SIGNS.map((s) => (
                            <button key={s} data-testid={`svd-sign-${i}-${s}`} disabled={postponed} onClick={() => toggle(f.home_team, f.away_team, s)}
                              className={`h-9 w-9 rounded-md border font-bold transition-colors ${cur === s ? "bg-[#00D95F] border-[#00D95F] text-[#08110A]" : "bg-[#0F1216] border-white/15 text-white hover:border-white/40"}`}>{s}</button>
                          ))}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
              <button data-testid="svd-submit" onClick={submit} disabled={selCount !== required} className="mt-3 w-full bg-[#F59E0B] text-[#1A1000] font-extrabold rounded-md py-3 flex items-center justify-center gap-2 disabled:opacity-50">
                <Send size={18} /> Invia {required} pronostici
              </button>
            </>
          )}
        </div>
      )}

      {(isAdmin || t.is_admin) && <InvitesManager basePath={`/sv/tournaments/${tid}`} />}
      {(isAdmin || t.is_admin) && <NotifyBox userIds={(participants || []).map((e) => e.user_id)} url={`/survival/${tid}`} />}

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4">
        <div className="flex items-center gap-2 mb-2">
          <BarChart3 size={16} className="text-[#F59E0B]" />
          <span className="text-sm font-bold flex-1">Storico / Riepilogo giornata</span>
          {mdList.length > 0 && (
            <select data-testid="svd-md-select" value={summaryMd || md?.id || ""} onChange={(e) => loadSummary(e.target.value)} className="bg-[#0F1216] border border-white/15 rounded-md px-2 py-1 text-xs">
              <option value="">Giornata…</option>
              {mdList.map((m) => <option key={m.id} value={m.id}>G{m.matchday ?? m.matchday_number} {m.status === "settled" ? "✓" : ""}</option>)}
            </select>
          )}
        </div>
        <button data-testid="svd-summary-btn" onClick={() => loadSummary()} className="w-full text-sm font-bold text-[#F59E0B] border border-[#F59E0B]/30 rounded-md py-2">
          Mostra riepilogo {md ? `giornata ${md.matchday ?? t.current_matchday}` : ""}
        </button>
        <button data-testid="svd-export-pdf" onClick={exportPdf} className="mt-2 w-full flex items-center justify-center gap-2 text-sm font-bold text-[#00D95F] border border-[#00D95F]/30 rounded-md py-2">
          <FileDown size={16} /> Esporta PDF (riepilogo + classifica)
        </button>
        {summary && (
          <div className="mt-3 space-y-3">
            <div className={`rounded-lg border p-3 flex items-start gap-2 text-xs ${summary.counts_hidden ? "border-white/10 bg-[#181D22] text-[#94A3B8]" : "border-[#F59E0B]/40 bg-[#F59E0B]/5 text-[#F8C471]"}`}>
              <BarChart3 size={15} className="shrink-0 mt-0.5" />
              <span>{summary.counts_hidden ? "Conteggi nascosti per privacy (pochi superstiti) fino al calcio d'inizio." : "Giornata visibile: ecco le scelte di tutti i partecipanti."}</span>
            </div>
            {(summary.fixtures || []).map((f, i) => {
              const c1 = f.counts?.["1"] ?? 0, cx = f.counts?.["X"] ?? 0, c2 = f.counts?.["2"] ?? 0;
              const total = c1 + cx + c2;
              const winner = c1 >= cx && c1 >= c2 ? "1" : cx >= c2 ? "X" : "2";
              return (
                <div key={i} className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="font-extrabold flex-1 truncate">{f.home_team}</span>
                    <span className="text-xs text-[#94A3B8]">vs</span>
                    <span className="font-extrabold flex-1 text-right truncate">{f.away_team}</span>
                  </div>
                  {summary.counts_hidden ? (
                    <div className="flex items-center gap-1.5 text-xs text-[#94A3B8] italic"><BarChart3 size={13} /> conteggi nascosti</div>
                  ) : (
                    <div className="flex gap-2">
                      {["1", "X", "2"].map((s) => {
                        const cnt = f.counts?.[s] ?? 0;
                        const isWinner = total > 0 && cnt > 0 && s === winner;
                        return (
                          <div key={s} data-testid={`svd-sum-${i}-${s}`} className={`flex-1 rounded-lg border py-2 flex flex-col items-center gap-0.5 ${isWinner ? "bg-[#EF4444]/15 border-[#EF4444]" : "bg-[#0F1216] border-white/10"}`}>
                            <div className={`text-sm font-extrabold ${isWinner ? "text-[#EF4444]" : "text-white"}`}>{s}</div>
                            <div className="flex items-center gap-1">
                              <span className={`font-extrabold ${isWinner ? "text-[#EF4444]" : "text-[#94A3B8]"}`}>{cnt}</span>
                              <Heart size={11} className={isWinner ? "text-[#EF4444] fill-[#EF4444]" : "text-[#64748B]"} />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div>
        <h2 className="text-lg font-extrabold mb-2 flex items-center gap-2"><Trophy size={18} className="text-[#F59E0B]" /> Classifica</h2>
        <p className="text-xs text-[#94A3B8] mb-2">Tocca un giocatore per vedere la sua giocata.</p>
        <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
          {participants.map((p) => {
            const out = p.eliminated;
            return (
              <button key={p.user_id} data-testid={`sv-lb-row-${p.user_id}`} onClick={() => setSelectedRow(p)} className={`w-full text-left px-3 py-3 flex items-center gap-3 hover:bg-white/5 transition-colors ${out ? "opacity-60" : ""}`}>
                <span className="w-8 text-[#EF4444] font-extrabold shrink-0">#{p.rank}</span>
                <div className="flex-1 min-w-0">
                  <div className={`font-bold truncate ${out ? "line-through" : ""}`}>{p.nickname}</div>
                  {out ? (
                    <div className="flex items-center gap-1 text-xs text-[#94A3B8]"><Skull size={12} /> {p.eliminated_matchday ? `Eliminato · G${p.eliminated_matchday}` : "Eliminato"}</div>
                  ) : (
                    <div className={`flex items-center gap-1 text-xs ${p.has_submitted_current ? "text-[#00D95F]" : "text-[#F59E0B]"}`}>
                      {p.has_submitted_current ? <CheckCircle2 size={12} /> : <Circle size={12} />}
                      {p.has_submitted_current ? "Giocata inserita" : "In attesa di giocata"}
                    </div>
                  )}
                </div>
                <div className="flex flex-col items-stretch gap-1 shrink-0">
                  <span data-testid={`sv-lb-prelives-${p.user_id}`} className="flex items-center justify-center gap-1 px-2 py-0.5 rounded-md bg-white/5 border border-white/10 text-xs font-bold text-[#94A3B8]"><Heart size={11} /> {p.pick_lives}</span>
                  <span data-testid={`sv-lb-bonus-${p.user_id}`} className="flex items-center justify-center gap-1 px-2 py-0.5 rounded-md bg-[#F59E0B]/10 border border-[#F59E0B]/50 text-xs font-bold text-[#F59E0B]"><Gift size={11} /> +{p.bonus_wins ?? 0}</span>
                  <span data-testid={`sv-lb-lives-${p.user_id}`} className="flex items-center justify-center gap-1 px-2 py-0.5 rounded-md bg-[#EF4444]/10 border border-[#EF4444]/50 text-xs font-extrabold text-[#EF4444]"><Heart size={11} className="fill-[#EF4444]" /> {p.lives_left}</span>
                </div>
                <ChevronRight size={18} className="text-[#94A3B8] shrink-0" />
              </button>
            );
          })}
        </div>
      </div>

      <SurvivaPicksModal tid={tid} row={selectedRow} onClose={() => setSelectedRow(null)} />
    </div>
  );
}
