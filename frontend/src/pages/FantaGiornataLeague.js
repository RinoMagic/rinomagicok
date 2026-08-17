import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronLeft, Trophy, Search, X, Send, Shirt, ListChecks } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import InvitesManager from "@/components/InvitesManager";
import NotifyBox from "@/components/NotifyBox";

const MODULES = {
  "3-4-3": { D: 3, C: 4, A: 3 }, "3-5-2": { D: 3, C: 5, A: 2 },
  "4-3-3": { D: 4, C: 3, A: 3 }, "4-4-2": { D: 4, C: 4, A: 2 },
  "4-5-1": { D: 4, C: 5, A: 1 }, "5-3-2": { D: 5, C: 3, A: 2 }, "5-4-1": { D: 5, C: 4, A: 1 },
};
const ROLES = [
  { k: "P", label: "Portieri", color: "#F59E0B" },
  { k: "D", label: "Difensori", color: "#00D95F" },
  { k: "C", label: "Centrocampisti", color: "#3B82F6" },
  { k: "A", label: "Attaccanti", color: "#EF4444" },
];
const empty = () => ({ P: { s: [], b: [] }, D: { s: [], b: [] }, C: { s: [], b: [] }, A: { s: [], b: [] } });

export default function FantaGiornataLeague() {
  const { leagueId } = useParams();
  const navigate = useNavigate();
  const { user, isAdmin } = useAuth();
  const [lg, setLg] = useState(null);
  const [tab, setTab] = useState("formazione");
  const [module, setModule] = useState("3-4-3");
  const [sel, setSel] = useState(empty());
  const [activeRole, setActiveRole] = useState("P");
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [board, setBoard] = useState([]);
  const [lineups, setLineups] = useState(null);
  const [fgResults, setFgResults] = useState(null);

  const md = lg?.current_matchday_number || 1;
  const needs = { P: { s: 1, b: 2 }, D: { s: MODULES[module].D, b: 2 }, C: { s: MODULES[module].C, b: 2 }, A: { s: MODULES[module].A, b: 2 } };

  const load = useCallback(async () => {
    try {
      const detail = await api(`/fg/leagues/${leagueId}`);
      setLg(detail);
      const mine = await api(`/fg/leagues/${leagueId}/lineups/${detail.current_matchday_number || 1}`)
        .then((r) => (r.members || []).find((m) => m.user_id === user.id)).catch(() => null);
      if (mine?.starters?.length) {
        const next = empty();
        if (mine.module && MODULES[mine.module]) setModule(mine.module);
        (mine.starters || []).forEach((p) => p && next[p.role].s.push(p));
        (mine.bench || []).forEach((p) => p && next[p.role].b.push(p));
        setSel(next);
      }
    } catch (e) { toast.error(e.message); }
  }, [leagueId, user.id]);
  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (tab === "classifica" || tab === "punteggi") api(`/fg/leagues/${leagueId}/leaderboard`).then((r) => setBoard(r.leaderboard || [])).catch(() => {});
    if (tab === "formazioni") api(`/fg/leagues/${leagueId}/lineups/${md}`).then(setLineups).catch(() => {});
    if (tab === "punteggi") api(`/fg/leagues/${leagueId}/results/${md}`).then(setFgResults).catch(() => setFgResults({ results: [] }));
  }, [tab, leagueId, md]);

  // Load members once for targeted notifications
  useEffect(() => {
    api(`/fg/leagues/${leagueId}/leaderboard`).then((r) => setBoard((b) => b.length ? b : (r.leaderboard || []))).catch(() => {});
  }, [leagueId]);

  const settleMd = async () => {
    try { await api(`/fg/leagues/${leagueId}/settle`, { method: "POST", body: { matchday: md } }); toast.success("Punti calcolati!"); api(`/fg/leagues/${leagueId}/results/${md}`).then(setFgResults); }
    catch (e) { toast.error(e.message); }
  };

  useEffect(() => {
    const t = setTimeout(() => {
      api(`/sal/players?role=${activeRole}&limit=30${q ? `&q=${encodeURIComponent(q)}` : ""}`)
        .then((r) => setResults(Array.isArray(r) ? r : (r.players || []))).catch(() => setResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [activeRole, q]);

  const chosenIds = new Set(Object.values(sel).flatMap((r) => [...r.s, ...r.b]).map((p) => p.id));

  const add = (p) => {
    if (chosenIds.has(p.id)) return;
    const bucket = sel[p.role];
    const n = needs[p.role];
    setSel((prev) => {
      const next = { ...prev, [p.role]: { s: [...bucket.s], b: [...bucket.b] } };
      if (next[p.role].s.length < n.s) next[p.role].s.push(p);
      else if (next[p.role].b.length < n.b) next[p.role].b.push(p);
      else { toast.warning(`${ROLES.find((r) => r.k === p.role).label}: reparto completo`); return prev; }
      return next;
    });
  };
  const remove = (role, list, id) => setSel((prev) => ({ ...prev, [role]: { ...prev[role], [list]: prev[role][list].filter((p) => p.id !== id) } }));

  const totalStarters = Object.values(sel).reduce((a, r) => a + r.s.length, 0);
  const totalBench = Object.values(sel).reduce((a, r) => a + r.b.length, 0);
  const complete = totalStarters === 11 && totalBench === 8 &&
    ROLES.every((r) => sel[r.k].s.length === needs[r.k].s && sel[r.k].b.length === needs[r.k].b);

  const submit = async () => {
    const starters = ROLES.flatMap((r) => sel[r.k].s.map((p) => p.id));
    const bench = ROLES.flatMap((r) => sel[r.k].b.map((p) => p.id));
    try {
      await api(`/fg/leagues/${leagueId}/lineup`, { method: "POST", body: { matchday: md, starters, bench, module } });
      toast.success("Formazione salvata!");
      load();
    } catch (e) { toast.error(e.message); }
  };

  if (!lg) return <div className="py-16 text-center text-[#94A3B8]">Caricamento...</div>;

  return (
    <div className="space-y-5">
      <button data-testid="fgl-back" onClick={() => navigate("/fanta")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm"><ChevronLeft size={16} /> Leghe</button>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4">
        <div className="text-2xl font-extrabold flex items-center gap-2"><Shirt className="text-[#A855F7]" size={22} /> {lg.name}</div>
        <div className="text-sm text-[#94A3B8] mt-1">Giornata {md} · {lg.members_count} partecipanti</div>
      </div>

      {(isAdmin || lg.is_admin) && <InvitesManager basePath={`/fg/leagues/${leagueId}`} />}
      {(isAdmin || lg.is_admin) && <NotifyBox userIds={board.map((m) => m.user_id)} url={`/fanta/${leagueId}`} />}

      <div className="flex gap-2 flex-wrap">
        {[["formazione", "Formazione"], ["classifica", "Classifica"], ["formazioni", "Formazioni"], ["punteggi", "Punteggi"]].map(([k, l]) => (
          <button key={k} data-testid={`fgl-tab-${k}`} onClick={() => setTab(k)} className={`px-4 py-2 rounded-md text-sm font-bold transition-colors ${tab === k ? "bg-[#A855F7] text-white" : "bg-[#181D22] border border-white/10 text-[#94A3B8]"}`}>{l}</button>
        ))}
      </div>

      {tab === "formazione" && (
        <div className="space-y-4">
          <div className="flex items-center gap-3">
            <label className="text-sm text-[#94A3B8]">Modulo</label>
            <select data-testid="fgl-module" value={module} onChange={(e) => setModule(e.target.value)} className="bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm">
              {Object.keys(MODULES).map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <span className={`ml-auto text-sm font-bold ${complete ? "text-[#00D95F]" : "text-[#F59E0B]"}`}>Titolari {totalStarters}/11 · Panca {totalBench}/8</span>
          </div>

          {/* Selected roster */}
          <div className="rounded-xl border border-white/10 bg-[#181D22] p-3 space-y-3">
            {ROLES.map((r) => (
              <div key={r.k}>
                <div className="text-xs uppercase tracking-widest mb-1" style={{ color: r.color }}>{r.label} — Titolari {sel[r.k].s.length}/{needs[r.k].s}, Panca {sel[r.k].b.length}/{needs[r.k].b}</div>
                <div className="flex flex-wrap gap-1.5">
                  {sel[r.k].s.map((p) => <Chip key={p.id} p={p} tone="s" onX={() => remove(r.k, "s", p.id)} />)}
                  {sel[r.k].b.map((p) => <Chip key={p.id} p={p} tone="b" onX={() => remove(r.k, "b", p.id)} />)}
                  {sel[r.k].s.length + sel[r.k].b.length === 0 && <span className="text-xs text-[#64748B]">nessun giocatore</span>}
                </div>
              </div>
            ))}
          </div>

          {/* Player picker */}
          <div className="rounded-xl border border-white/10 bg-[#181D22] p-3 space-y-3">
            <div className="flex gap-2 overflow-x-auto">
              {ROLES.map((r) => (
                <button key={r.k} data-testid={`fgl-role-${r.k}`} onClick={() => setActiveRole(r.k)} className={`shrink-0 px-3 py-1.5 rounded-md text-sm font-bold ${activeRole === r.k ? "text-black" : "text-[#94A3B8] bg-[#0F1216] border border-white/10"}`} style={activeRole === r.k ? { backgroundColor: r.color } : {}}>{r.label}</button>
              ))}
            </div>
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-[#64748B]" size={16} />
              <input data-testid="fgl-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Cerca giocatore..." className="w-full bg-[#0F1216] border border-white/15 rounded-md pl-9 pr-3 py-2 text-sm" />
            </div>
            <div className="max-h-64 overflow-y-auto divide-y divide-white/10">
              {results.map((p) => {
                const taken = chosenIds.has(p.id);
                return (
                  <button key={p.id} data-testid={`fgl-add-${p.id}`} disabled={taken} onClick={() => add(p)} className={`w-full flex items-center gap-2 py-2 text-left ${taken ? "opacity-40" : "hover:bg-white/5"}`}>
                    <span className="text-xs font-bold w-6" style={{ color: ROLES.find((r) => r.k === p.role)?.color }}>{p.role}</span>
                    <span className="flex-1 text-sm">{p.full_name}</span>
                    <span className="text-xs text-[#94A3B8]">{p.team}</span>
                  </button>
                );
              })}
              {results.length === 0 && <div className="py-4 text-center text-xs text-[#64748B]">Nessun risultato</div>}
            </div>
          </div>

          <button data-testid="fgl-submit" onClick={submit} disabled={!complete} className="w-full bg-[#F59E0B] text-[#1A1000] font-extrabold rounded-md py-3 flex items-center justify-center gap-2 disabled:opacity-50">
            <Send size={18} /> Salva formazione
          </button>
        </div>
      )}

      {tab === "classifica" && (
        <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
          <div className="px-5 py-3 text-xs tracking-widest uppercase text-[#94A3B8] flex items-center gap-2"><Trophy size={14} className="text-[#F59E0B]" /> Classifica generale</div>
          {board.map((r, i) => (
            <div key={r.user_id} data-testid={`fgl-board-${r.user_id}`} className="px-5 py-3 flex items-center gap-3">
              <span className="w-6 text-[#94A3B8] font-bold">{i + 1}</span>
              <span className="flex-1 font-medium">{r.nickname}</span>
              <span className="text-xs text-[#94A3B8]">{r.matchdays_played} gg</span>
              <span className="font-extrabold text-[#A855F7]">{r.total}</span>
            </div>
          ))}
        </div>
      )}

      {tab === "formazioni" && lineups && (
        <div className="space-y-3">
          <div className="text-sm text-[#94A3B8] flex items-center gap-2"><ListChecks size={16} /> {lineups.deadline_passed ? "Formazioni di tutti — giornata " + md : "Le formazioni degli altri saranno visibili dopo la scadenza."}</div>
          {(lineups.members || []).map((m) => (
            <div key={m.user_id} data-testid={`fgl-lineup-${m.user_id}`} className="rounded-xl border border-white/10 bg-[#181D22] p-4">
              <div className="flex items-center justify-between">
                <span className="font-bold">{m.nickname}</span>
                <span className="text-xs text-[#94A3B8]">{m.has_lineup ? (m.module || "inviata") : "nessuna formazione"}</span>
              </div>
              {m.hidden ? <div className="text-xs text-[#64748B] mt-1">nascosta fino alla scadenza</div>
              : m.starters ? (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {m.starters.map((p, i) => p && <span key={i} className="text-xs px-2 py-0.5 rounded bg-white/10">{p.full_name}</span>)}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
      {tab === "punteggi" && (
        <div className="space-y-3">
          {(isAdmin || lg.is_admin) && (
            <button data-testid="fgl-settle" onClick={settleMd} className="w-full bg-[#EF4444] text-white font-bold rounded-md py-2.5 text-sm">Calcola punti giornata {md} (dai voti caricati)</button>
          )}
          <div className="rounded-xl border border-white/10 bg-[#181D22] divide-y divide-white/10">
            <div className="px-5 py-3 text-xs uppercase tracking-widest text-[#94A3B8]">Punti giornata {md}</div>
            {(!fgResults || (fgResults.results || []).length === 0) ? (
              <div className="px-5 py-6 text-center text-[#94A3B8] text-sm">Nessun punteggio: carica i voti dal Pannello Admin, poi premi "Calcola punti".</div>
            ) : fgResults.results.map((r, i) => (
              <div key={r.user_id} data-testid={`fgl-pts-${r.user_id}`} className="px-5 py-3">
                <div className="flex items-center gap-3">
                  <span className="w-6 text-[#94A3B8] font-bold">{i + 1}</span>
                  <span className="flex-1 font-medium">{r.nickname}</span>
                  <span className="font-extrabold text-[#A855F7]">{Number(r.total_fantavoto ?? r.total ?? 0).toFixed(1)}</span>
                </div>
                {Array.isArray(r.breakdown) && r.breakdown.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {r.breakdown.map((b, j) => (<span key={j} className="text-[11px] px-2 py-0.5 rounded bg-white/10">{(b.full_name || b.player_name)}: {b.fantavoto ?? b.vote ?? "-"}</span>))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Chip({ p, tone, onX }) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded ${tone === "s" ? "bg-[#A855F7]/25 text-white" : "bg-white/10 text-[#94A3B8]"}`}>
      {p.full_name}
      <button onClick={onX} className="hover:text-[#EF4444]"><X size={12} /></button>
    </span>
  );
}
