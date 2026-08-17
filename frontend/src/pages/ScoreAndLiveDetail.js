import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronLeft, Heart, Skull, Send, Copy, Trophy, BarChart3 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import InvitesManager from "@/components/InvitesManager";
import NotifyBox from "@/components/NotifyBox";

export default function ScoreAndLiveDetail() {
  const { tid } = useParams();
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const [t, setT] = useState(null);
  const [md, setMd] = useState(null);
  const [playersByTeam, setPlayersByTeam] = useState({});
  const [sel, setSel] = useState({}); // fixture_idx -> player_id
  const [summary, setSummary] = useState(null);
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const tour = await api(`/sal/tournaments/${tid}`);
      setT(tour);
      const open = (tour.matchdays || []).find((m) => m.status === "open");
      if (open) {
        const detail = await api(`/sal/tournaments/${tid}/matchdays/${open.id}`);
        setMd(detail);
        const pre = {};
        (detail.my_picks?.picks || []).forEach((p) => { pre[p.fixture_idx] = p.player_id; });
        setSel(pre);
        const teams = [...new Set((detail.fixtures || []).flatMap((f) => [f.home_team, f.away_team]))];
        const map = {};
        await Promise.all(teams.map(async (tm) => {
          try {
            const r = await api(`/sal/players?team=${encodeURIComponent(tm)}`);
            map[tm] = Array.isArray(r) ? r : (r.players || []);
          } catch { map[tm] = []; }
        }));
        setPlayersByTeam(map);
      } else setMd(null);
    } catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, [tid]);
  useEffect(() => { load(); }, [load]);

  const required = md?.expected_picks_count || 0;
  const selCount = Object.values(sel).filter(Boolean).length;

  const submit = async () => {
    const picks = Object.entries(sel).filter(([, pid]) => pid).map(([idx, pid]) => ({ fixture_idx: Number(idx), player_id: pid }));
    if (picks.length !== required) { toast.error(`Devi scegliere esattamente ${required} marcatori.`); return; }
    try {
      await api(`/sal/tournaments/${tid}/matchdays/${md.id}/picks`, { method: "POST", body: { picks } });
      toast.success("Marcatori inviati!");
      load();
    } catch (e) { toast.error(e.message); }
  };

  const loadSummary = async () => {
    if (!md) { toast.info("Nessuna giornata attiva"); return; }
    try { setSummary(await api(`/sal/tournaments/${tid}/matchdays/${md.id}/summary`)); }
    catch (e) { toast.error(e.message); }
  };

  const loadHistory = async () => {
    try { setHistory(await api(`/sal/tournaments/${tid}/history`)); }
    catch (e) { toast.error(e.message); }
  };

  if (loading || !t) return <div className="py-16 text-center text-[#94A3B8]">Caricamento...</div>;
  const parts = t.participants || [];
  return (
    <div className="space-y-5">
      <button data-testid="sald-back" onClick={() => navigate("/scoreandlive")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Tornei</button>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4">
        <div className="text-2xl font-extrabold">{t.name}</div>
        <div className="flex flex-wrap items-center gap-4 mt-2 text-sm text-[#94A3B8]">
          <span className="text-[#EF4444] flex items-center gap-1"><Heart size={14} /> {t.players_alive ?? parts.filter(p=>!p.eliminated_at_matchday).length}/{t.players_total ?? parts.length}</span>
          {md && <span>Giornata {md.matchday_number}</span>}
          {t.status === "finished" && <span className="text-[#F59E0B] font-bold">Concluso</span>}
        </div>
        {(isAdmin || t.is_admin) && t.invite_code && (
          <button data-testid="sald-copy" onClick={() => { navigator.clipboard?.writeText(t.invite_code); toast.success("Codice copiato"); }} className="mt-3 inline-flex items-center gap-2 text-xs bg-white/10 rounded-md px-3 py-1.5"><Copy size={13} /> Codice: <b>{t.invite_code}</b></button>
        )}
      </div>

      {md && required > 0 && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-lg font-extrabold">Scegli i marcatori · G{md.matchday_number}</h2>
            <span className={`text-sm font-bold ${selCount === required ? "text-[#00D95F]" : "text-[#F59E0B]"}`}>{selCount}/{required}</span>
          </div>
          <p className="text-xs text-[#94A3B8] mb-2">Scegli {required} marcatori (uno per vita), ognuno in una partita diversa.</p>
          <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
            {(md.fixtures || []).filter((f) => !f.postponed_before).map((f) => {
              const opts = [...(playersByTeam[f.home_team] || []), ...(playersByTeam[f.away_team] || [])];
              return (
                <div key={f.idx} data-testid={`sald-fixture-${f.idx}`} className="px-3 py-3">
                  <div className="text-sm mb-1">{f.home_team} - {f.away_team}</div>
                  <select data-testid={`sald-sel-${f.idx}`} value={sel[f.idx] || ""} onChange={(e) => setSel((p) => ({ ...p, [f.idx]: e.target.value }))}
                    className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm">
                    <option value="">— nessun marcatore —</option>
                    {opts.map((pl) => <option key={pl.id} value={pl.id}>{pl.full_name} ({pl.team})</option>)}
                  </select>
                </div>
              );
            })}
          </div>
          <button data-testid="sald-submit" onClick={submit} disabled={selCount !== required} className="mt-3 w-full bg-[#F59E0B] text-[#1A1000] font-extrabold rounded-md py-3 flex items-center justify-center gap-2 disabled:opacity-50">
            <Send size={18} /> Invia {required} marcatori
          </button>
        </div>
      )}

      {(isAdmin || t.is_admin) && <InvitesManager basePath={`/sal/tournaments/${tid}`} />}
      {(isAdmin || t.is_admin) && <NotifyBox userIds={parts.map((p) => p.user_id)} url={`/scoreandlive/${tid}`} />}

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4">
        <button data-testid="sald-history-btn" onClick={loadHistory} className="w-full flex items-center justify-center gap-2 text-sm font-bold text-[#3B82F6]">
          <BarChart3 size={16} /> Storico giornate
        </button>
        {history && (
          <div className="mt-3 space-y-3">
            {(history.matchdays || []).filter((m) => m.status !== "open" || m.picks_visible).map((m) => (
              <div key={m.id} data-testid={`sald-hist-${m.matchday_number}`} className="rounded-lg bg-[#0F1216] p-3">
                <div className="text-sm font-bold flex items-center justify-between">
                  <span>Giornata {m.matchday_number}</span>
                  <span className="text-xs text-[#94A3B8] uppercase">{m.status}</span>
                </div>
                {(m.scorers || []).length > 0 && (
                  <div className="text-xs text-[#00D95F] mt-1">Marcatori: {m.scorers.map((s) => s.player_name || s.player_id).join(", ")}</div>
                )}
                {(m.picks || []).length > 0 && (
                  <div className="mt-2 space-y-0.5">
                    {m.picks.map((pk, j) => (
                      <div key={j} className="text-xs flex justify-between">
                        <span className="text-white">{pk.nickname}</span>
                        <span className={pk.outcome === "survived" ? "text-[#00D95F]" : pk.outcome === "eliminated" ? "text-[#EF4444]" : "text-[#94A3B8]"}>{pk.outcome || "—"}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {(history.matchdays || []).length === 0 && <div className="text-xs text-[#64748B]">Nessuna giornata giocata.</div>}
          </div>
        )}
      </div>

      <div>
        <h2 className="text-lg font-extrabold mb-2 flex items-center gap-2"><BarChart3 size={18} className="text-[#3B82F6]" /> Riepilogo giornata</h2>
        <button data-testid="sald-summary-btn" onClick={loadSummary} className="w-full text-sm font-bold text-[#3B82F6] border border-[#3B82F6]/30 rounded-md py-2 mb-2">Mostra riepilogo giornata corrente</button>
        {summary && (
          <div className="mt-3 space-y-2">
            {(summary.fixtures || []).map((f, i) => (
              <div key={i} className="rounded-lg bg-[#0F1216] p-3">
                <div className="text-sm font-medium">{f.home_team} - {f.away_team}</div>
                {(f.candidates || []).length === 0 ? (
                  <div className="text-xs text-[#64748B] mt-1">Nessun marcatore scelto (o nascosto per privacy).</div>
                ) : (
                  <div className="mt-1 space-y-1">
                    {f.candidates.map((c, j) => (
                      <div key={j} className="text-xs flex items-center gap-2">
                        <span className="text-white">{c.player_name}</span>
                        <span className="text-[#94A3B8]">×{c.count}</span>
                        {Array.isArray(c.pickers) && c.pickers.length > 0 && (
                          <span className="text-[#64748B]">— {c.pickers.map((p) => p.nickname).join(", ")}</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <h2 className="text-lg font-extrabold mb-2 flex items-center gap-2"><Trophy size={18} className="text-[#F59E0B]" /> Classifica</h2>
        <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
          {parts.map((p, i) => {
            const out = p.eliminated_at_matchday != null;
            return (
              <div key={p.user_id} data-testid={`sald-part-${p.user_id}`} className="px-4 py-3 flex items-center gap-3">
                <span className="w-6 text-[#94A3B8] font-bold">{i + 1}</span>
                {out ? <Skull size={16} className="text-white/40" /> : <Heart size={16} className="text-[#EF4444]" />}
                <span className={`flex-1 ${out ? "line-through text-white/40" : "font-medium"}`}>{p.nickname}</span>
                <span className="text-sm font-bold" style={{ color: out ? "#64748B" : "#EF4444" }}>{out ? `G${p.eliminated_at_matchday}` : `${p.lives_remaining} ♥`}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
