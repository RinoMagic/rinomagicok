import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import api, { apiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Skull, Heart, Plus, CheckCircle2, ChevronLeft, ShieldCheck } from "lucide-react";

const OPTIONS = ["1", "X", "2"];

export default function Survival() {
  const { isAdmin } = useAuth();
  const [tournaments, setTournaments] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [startMd, setStartMd] = useState(1);

  const loadList = useCallback(async () => {
    const { data } = await api.get("/survival/tournaments");
    setTournaments(data.tournaments);
  }, []);

  const loadDetail = useCallback(async (id) => {
    const { data } = await api.get(`/survival/tournaments/${id}`);
    setDetail(data);
  }, []);

  useEffect(() => { loadList(); }, [loadList]);
  useEffect(() => { if (activeId) loadDetail(activeId); }, [activeId, loadDetail]);

  const join = async (id) => {
    try { await api.post(`/survival/tournaments/${id}/join`); toast.success("Iscritto!"); loadList(); setActiveId(id); }
    catch (e) { toast.error(apiError(e)); }
  };

  const create = async () => {
    try {
      await api.post("/survival/tournaments", { name, start_matchday: Number(startMd) });
      toast.success("Torneo creato!"); setName(""); setCreating(false); loadList();
    } catch (e) { toast.error(apiError(e)); }
  };

  const pick = async (team) => {
    try {
      await api.post(`/survival/tournaments/${activeId}/pick`, { matchday: detail.tournament.current_matchday, team });
      toast.success(`Hai scelto ${team}!`); loadDetail(activeId);
    } catch (e) { toast.error(apiError(e)); }
  };

  if (activeId && detail) {
    return <Detail detail={detail} onBack={() => { setActiveId(null); setDetail(null); loadList(); }} onPick={pick} isAdmin={isAdmin} onResolved={() => loadDetail(activeId)} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-display text-4xl">SURVIVAL</h1>
          <p className="text-zinc-400 text-sm">Scegli una squadra vincente ogni giornata. Sbagli, sei fuori.</p>
        </div>
        {isAdmin && (
          <button data-testid="survival-admin-toggle" onClick={() => setCreating((v) => !v)} className="text-sm bg-white/10 hover:bg-white/20 border border-white/15 rounded-sm px-3 py-2 flex items-center gap-2 transition-colors">
            <Plus size={16} /> Nuovo
          </button>
        )}
      </div>

      {isAdmin && creating && (
        <div className="rounded-sm border border-[#0057B8]/40 bg-[#0057B8]/5 p-5 space-y-3">
          <div className="text-xs uppercase tracking-[0.2em] text-[#00FF66]">Crea torneo Survival</div>
          <div className="grid sm:grid-cols-3 gap-3">
            <input data-testid="survival-name-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Nome torneo" className="sm:col-span-2 bg-[#0a0a0a] border border-white/15 rounded-sm px-3 py-2 text-sm" />
            <input data-testid="survival-startmd-input" type="number" min="1" max="38" value={startMd} onChange={(e) => setStartMd(e.target.value)} placeholder="Giornata iniziale" className="bg-[#0a0a0a] border border-white/15 rounded-sm px-3 py-2 text-sm" />
          </div>
          <button data-testid="survival-create-button" onClick={create} disabled={!name} className="bg-[#0057B8] hover:bg-[#00438F] disabled:opacity-40 text-white text-sm font-semibold rounded-sm px-4 py-2 transition-colors">Crea</button>
        </div>
      )}

      {tournaments.length === 0 ? (
        <div className="rounded-sm border border-white/10 bg-[#141414] px-5 py-12 text-center text-zinc-500">Nessun torneo Survival attivo.</div>
      ) : (
        <div className="grid sm:grid-cols-2 gap-4">
          {tournaments.map((t) => (
            <div key={t.id} data-testid={`survival-card-${t.id}`} className="rounded-sm border border-white/10 bg-[#141414] p-5">
              <div className="flex items-center justify-between">
                <Skull className="text-[#00FF66]" size={22} />
                <span className="text-[10px] tracking-[0.2em] uppercase text-zinc-400">Giornata {t.current_matchday}</span>
              </div>
              <div className="font-display text-3xl mt-3">{t.name}</div>
              <div className="text-sm text-zinc-400 mt-1">{t.participant_count} partecipanti</div>
              <div className="mt-4 flex gap-2">
                {t.joined ? (
                  <button data-testid={`survival-open-${t.id}`} onClick={() => setActiveId(t.id)} className="flex-1 bg-white text-black font-semibold rounded-sm py-2 text-sm hover:bg-zinc-200 transition-colors">Apri</button>
                ) : (
                  <>
                    <button data-testid={`survival-join-${t.id}`} onClick={() => join(t.id)} className="flex-1 bg-[#0057B8] hover:bg-[#00438F] text-white font-semibold rounded-sm py-2 text-sm transition-colors">Iscriviti</button>
                    <button data-testid={`survival-view-${t.id}`} onClick={() => setActiveId(t.id)} className="px-4 border border-white/15 rounded-sm py-2 text-sm text-zinc-300 hover:text-white transition-colors">Vedi</button>
                  </>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Detail({ detail, onBack, onPick, isAdmin, onResolved }) {
  const { tournament: t, entries, my_entry, current_fixtures, my_pick } = detail;
  const [results, setResults] = useState({});
  const teams = [...new Set(current_fixtures.flatMap((f) => [f.home_team, f.away_team]))].sort();
  const used = my_entry?.used_teams || [];
  const alive = my_entry?.status === "alive";
  const aliveCount = entries.filter((e) => e.status === "alive").length;

  const resolve = async () => {
    try {
      await api.post(`/survival/tournaments/${t.id}/resolve`, { matchday: t.current_matchday, results });
      toast.success("Giornata risolta!"); setResults({}); onResolved();
    } catch (e) { toast.error(apiError(e)); }
  };

  return (
    <div className="space-y-6">
      <button data-testid="survival-back" onClick={onBack} className="flex items-center gap-1 text-zinc-400 hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Tornei</button>

      <div className="rounded-sm border border-white/10 bg-[#141414] p-5">
        <div className="font-display text-4xl">{t.name}</div>
        <div className="flex items-center gap-4 mt-2 text-sm">
          <span className="text-zinc-400">Giornata <b className="text-white">{t.current_matchday}</b></span>
          <span className="text-zinc-400">Superstiti <b className="text-[#00FF66]">{aliveCount}</b></span>
          {my_entry && (
            <span className={`flex items-center gap-1 ${alive ? "text-[#00FF66]" : "text-[#E32221]"}`}>
              {alive ? <Heart size={14} /> : <Skull size={14} />} {alive ? "In gioco" : `Eliminato (G${my_entry.eliminated_matchday})`}
            </span>
          )}
        </div>
      </div>

      {my_entry && alive && (
        <div>
          <h2 className="font-display text-2xl mb-3">SCEGLI LA SQUADRA · G{t.current_matchday}</h2>
          {my_pick && <div className="mb-3 text-sm text-[#00FF66]">Scelta attuale: <b>{my_pick.team}</b> (puoi cambiarla)</div>}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
            {teams.map((team) => {
              const isUsed = used.includes(team) && my_pick?.team !== team;
              const selected = my_pick?.team === team;
              return (
                <button key={team} data-testid={`survival-team-${team}`} disabled={isUsed} onClick={() => onPick(team)}
                  className={`rounded-sm border px-3 py-3 text-sm font-medium transition-colors duration-200 ${
                    selected ? "bg-[#00FF66] border-[#00FF66] text-black"
                    : isUsed ? "bg-[#0a0a0a] border-white/5 text-zinc-600 line-through cursor-not-allowed"
                    : "bg-[#141414] border-white/15 text-white hover:border-white/40"}`}>
                  {team}
                </button>
              );
            })}
          </div>
          {used.length > 0 && <p className="text-xs text-zinc-500 mt-3">Squadre già usate: {used.join(", ")}</p>}
        </div>
      )}

      {my_entry && !alive && (
        <div className="rounded-sm border border-[#E32221]/40 bg-[#E32221]/10 p-5 text-center text-[#E32221] font-semibold flex items-center justify-center gap-2">
          <Skull size={18} /> Sei stato eliminato alla giornata {my_entry.eliminated_matchday}.
        </div>
      )}

      {isAdmin && (
        <div className="rounded-sm border border-[#0057B8]/40 bg-[#0057B8]/5 p-5">
          <div className="text-xs uppercase tracking-[0.2em] text-[#00FF66] mb-3 flex items-center gap-2"><ShieldCheck size={14} /> Admin · Risolvi giornata {t.current_matchday}</div>
          <div className="space-y-2">
            {current_fixtures.map((f) => (
              <div key={f.id} className="flex items-center justify-between gap-3">
                <span className="text-sm flex-1">{f.home_team} - {f.away_team}</span>
                <div className="flex gap-1">
                  {OPTIONS.map((o) => (
                    <button key={o} data-testid={`survival-res-${f.id}-${o}`} onClick={() => setResults((r) => ({ ...r, [f.id]: o }))}
                      className={`h-9 w-9 rounded-sm border font-display text-lg transition-colors ${results[f.id] === o ? "bg-[#00FF66] border-[#00FF66] text-black" : "bg-[#0a0a0a] border-white/15 text-zinc-300 hover:border-white/40"}`}>{o}</button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <button data-testid="survival-resolve-button" onClick={resolve} className="mt-3 bg-[#00FF66] hover:bg-[#00cc52] text-black text-sm font-semibold rounded-sm px-4 py-2 flex items-center gap-2 transition-colors">
            <CheckCircle2 size={16} /> Risolvi ed elimina
          </button>
        </div>
      )}

      <div className="rounded-sm border border-white/10 bg-[#141414] divide-y divide-white/10">
        <div className="px-5 py-3 text-xs tracking-[0.2em] uppercase text-zinc-500">Partecipanti ({entries.length})</div>
        {entries.map((e) => (
          <div key={e.user_id} data-testid={`survival-entry-${e.user_id}`} className="px-5 py-3 flex items-center gap-3">
            {e.status === "alive" ? <Heart size={16} className="text-[#00FF66]" /> : <Skull size={16} className="text-zinc-600" />}
            <span className={`flex-1 ${e.status === "alive" ? "font-medium" : "text-zinc-500 line-through"}`}>{e.nickname}</span>
            <span className="text-xs text-zinc-500">{e.status === "alive" ? `${(e.used_teams || []).length} scelte` : `Fuori G${e.eliminated_matchday}`}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
