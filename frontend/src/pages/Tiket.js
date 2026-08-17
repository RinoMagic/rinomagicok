import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import api, { apiError } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { Star, Trophy, Plus, CheckCircle2, Send } from "lucide-react";

const OPTIONS = ["1", "X", "2"];

function Segmented({ value, onChange, disabled, testidPrefix }) {
  return (
    <div className="flex gap-1">
      {OPTIONS.map((o) => (
        <button
          key={o}
          data-testid={`${testidPrefix}-${o}`}
          disabled={disabled}
          onClick={() => onChange(o)}
          className={`h-10 w-10 rounded-sm border font-display text-xl transition-colors duration-200 ${
            value === o
              ? "bg-[#00FF66] border-[#00FF66] text-black"
              : "bg-[#0a0a0a] border-white/15 text-zinc-300 hover:border-white/40 disabled:opacity-40"
          }`}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

export default function Tiket() {
  const { isAdmin } = useAuth();
  const [tab, setTab] = useState("gioca");
  const [rounds, setRounds] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [preds, setPreds] = useState({});
  const [bonus, setBonus] = useState(false);
  const [standings, setStandings] = useState([]);
  const [adminOpen, setAdminOpen] = useState(false);
  const [results, setResults] = useState({});

  const loadRounds = useCallback(async () => {
    const { data } = await api.get("/tiket/rounds");
    setRounds(data.rounds);
    if (data.rounds.length && !activeId) setActiveId(data.rounds[0].id);
  }, [activeId]);

  useEffect(() => { loadRounds(); }, [loadRounds]);
  useEffect(() => {
    if (tab === "classifica") api.get("/tiket/standings").then(({ data }) => setStandings(data.standings));
  }, [tab]);

  useEffect(() => {
    if (!activeId) return;
    api.get(`/tiket/rounds/${activeId}`).then(({ data }) => {
      setDetail(data);
      setPreds(data.my_schedina?.predictions || {});
      setBonus(data.my_schedina?.big_match_bonus || false);
      setResults(data.round?.results || {});
    });
  }, [activeId]);

  const submit = async () => {
    try {
      await api.post(`/tiket/rounds/${activeId}/schedina`, { predictions: preds, big_match_bonus: bonus });
      toast.success("Schedina salvata!");
      api.get(`/tiket/rounds/${activeId}`).then(({ data }) => setDetail(data));
    } catch (e) { toast.error(apiError(e)); }
  };

  const round = detail?.round;
  const scored = round?.status === "scored";
  const allFilled = round && Object.keys(preds).length === round.fixtures.length;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="font-display text-4xl flex items-center gap-2"><span>TIKET</span></h1>
          <p className="text-zinc-400 text-sm">Pronostica 1 · X · 2 di ogni partita</p>
        </div>
        {isAdmin && (
          <button data-testid="tiket-admin-toggle" onClick={() => setAdminOpen((v) => !v)} className="text-sm bg-white/10 hover:bg-white/20 border border-white/15 rounded-sm px-3 py-2 flex items-center gap-2 transition-colors">
            <Plus size={16} /> Admin
          </button>
        )}
      </div>

      {isAdmin && adminOpen && <AdminPanel rounds={rounds} onCreated={loadRounds} activeRound={round} results={results} setResults={setResults} onScored={() => { loadRounds(); api.get(`/tiket/rounds/${activeId}`).then(({ data }) => setDetail(data)); }} />}

      <div className="flex gap-2">
        {["gioca", "classifica"].map((t) => (
          <button key={t} data-testid={`tiket-tab-${t}`} onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-sm text-sm font-semibold uppercase tracking-wider transition-colors duration-200 ${tab === t ? "bg-[#0057B8] text-white" : "bg-[#141414] border border-white/10 text-zinc-400 hover:text-white"}`}>
            {t === "gioca" ? "Gioca" : "Classifica"}
          </button>
        ))}
      </div>

      {tab === "gioca" && (
        <>
          {rounds.length === 0 ? (
            <Empty text="Nessuna giornata Tiket aperta al momento." />
          ) : (
            <>
              <div className="flex gap-2 overflow-x-auto pb-1">
                {rounds.map((r) => (
                  <button key={r.id} data-testid={`tiket-round-${r.matchday}`} onClick={() => setActiveId(r.id)}
                    className={`shrink-0 px-4 py-2 rounded-sm border text-sm transition-colors duration-200 ${activeId === r.id ? "bg-white text-black border-white" : "bg-[#141414] border-white/10 text-zinc-400 hover:text-white"}`}>
                    G{r.matchday} {r.status === "scored" ? "✓" : ""}
                  </button>
                ))}
              </div>

              {round && (
                <div className="space-y-3">
                  <div className="rounded-sm border border-white/10 bg-[#141414] divide-y divide-white/10">
                    {round.fixtures.map((f) => {
                      const isBig = f.id === round.big_match_fixture_id;
                      const res = round.results?.[f.id];
                      const myPred = preds[f.id];
                      const correct = scored && myPred && myPred === res;
                      return (
                        <div key={f.id} data-testid={`tiket-fixture-${f.id}`} className={`px-4 py-3 ${isBig ? "bg-[#0057B8]/10" : ""}`}>
                          {isBig && <div className="text-[10px] tracking-[0.2em] uppercase text-[#00FF66] flex items-center gap-1 mb-1"><Star size={12} /> Big Match {round.fixtures && "(x2)"}</div>}
                          <div className="flex items-center justify-between gap-3">
                            <div className="flex-1 min-w-0 text-sm">
                              <span className="font-medium">{f.home_team}</span>
                              <span className="text-zinc-500 mx-1">-</span>
                              <span className="font-medium">{f.away_team}</span>
                            </div>
                            <Segmented value={myPred} onChange={(v) => setPreds((p) => ({ ...p, [f.id]: v }))} disabled={scored} testidPrefix={`tiket-pred-${f.id}`} />
                          </div>
                          {scored && (
                            <div className="mt-1 text-xs flex items-center gap-2">
                              <span className="text-zinc-500">Esito: <b className="text-white">{res || "-"}</b></span>
                              {myPred && <span className={correct ? "text-[#00FF66]" : "text-[#E32221]"}>{correct ? "✓ Indovinato" : "✗ Sbagliato"}</span>}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {!scored && (
                    <>
                      <label className="flex items-center gap-3 rounded-sm border border-white/10 bg-[#141414] px-4 py-3 cursor-pointer">
                        <input data-testid="tiket-bonus-checkbox" type="checkbox" checked={bonus} onChange={(e) => setBonus(e.target.checked)} className="h-4 w-4 accent-[#00FF66]" />
                        <span className="text-sm">Attiva <b className="text-[#00FF66]">Bonus Big Match</b> (x3 se indovini la partita clou)</span>
                      </label>
                      <button data-testid="tiket-submit-schedina" onClick={submit} disabled={!allFilled}
                        className="w-full bg-[#0057B8] hover:bg-[#00438F] disabled:opacity-40 text-white font-semibold rounded-sm py-3 flex items-center justify-center gap-2 transition-colors duration-200">
                        <Send size={18} /> {detail?.my_schedina ? "Aggiorna schedina" : "Invia schedina"} {!allFilled && "(completa tutti i pronostici)"}
                      </button>
                    </>
                  )}
                  {scored && detail?.my_schedina && (
                    <div className="rounded-sm border border-[#00FF66]/30 bg-[#00FF66]/10 p-4 text-center">
                      <div className="text-xs uppercase tracking-widest text-zinc-400">Il tuo punteggio</div>
                      <div className="font-display text-5xl text-[#00FF66]">{detail.my_schedina.points ?? 0}</div>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </>
      )}

      {tab === "classifica" && (
        <div className="rounded-sm border border-white/10 bg-[#141414] divide-y divide-white/10">
          <div className="px-5 py-3 text-xs tracking-[0.2em] uppercase text-zinc-500 flex items-center gap-2"><Trophy size={14} className="text-[#00FF66]" /> Classifica Generale</div>
          {standings.length === 0 ? <div className="px-5 py-10 text-center text-zinc-500">Ancora nessun punteggio.</div> :
            standings.map((s) => (
              <div key={s.user_id} data-testid={`tiket-standing-${s.user_id}`} className="px-5 py-3 flex items-center gap-4">
                <span className="font-display text-2xl w-8 text-zinc-500">{s.rank}</span>
                <span className="flex-1 font-medium">{s.nickname}</span>
                <span className="text-xs text-zinc-500">{s.played} giocate</span>
                <span className="font-display text-2xl text-[#00FF66]">{s.points}</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

function Empty({ text }) {
  return <div className="rounded-sm border border-white/10 bg-[#141414] px-5 py-12 text-center text-zinc-500">{text}</div>;
}

function AdminPanel({ rounds, onCreated, activeRound, results, setResults, onScored }) {
  const [matchdays, setMatchdays] = useState([]);
  const [md, setMd] = useState("");
  const [deadline, setDeadline] = useState("");
  const [fixtures, setFixtures] = useState([]);
  const [bigMatch, setBigMatch] = useState("");

  useEffect(() => { api.get("/serie-a/matchdays").then(({ data }) => setMatchdays(data.matchdays)); }, []);
  useEffect(() => {
    if (!md) { setFixtures([]); return; }
    api.get(`/serie-a/calendar?matchday=${md}`).then(({ data }) => { setFixtures(data.matches); setBigMatch(data.matches[0]?.id || ""); });
  }, [md]);

  const create = async () => {
    try {
      const iso = deadline ? new Date(deadline).toISOString() : new Date(Date.now() + 3 * 864e5).toISOString();
      await api.post("/tiket/rounds", { matchday: Number(md), deadline: iso, big_match_fixture_id: bigMatch });
      toast.success("Giornata Tiket creata!");
      onCreated();
    } catch (e) { toast.error(apiError(e)); }
  };

  const saveResults = async () => {
    try {
      await api.post(`/tiket/rounds/${activeRound.id}/results`, { results });
      toast.success("Risultati salvati e classifica aggiornata!");
      onScored();
    } catch (e) { toast.error(apiError(e)); }
  };

  const takenMds = rounds.map((r) => r.matchday);

  return (
    <div className="rounded-sm border border-[#0057B8]/40 bg-[#0057B8]/5 p-5 space-y-5">
      <div>
        <div className="text-xs uppercase tracking-[0.2em] text-[#00FF66] mb-3">Crea nuova giornata</div>
        <div className="grid sm:grid-cols-3 gap-3">
          <select data-testid="admin-tiket-md-select" value={md} onChange={(e) => setMd(e.target.value)} className="bg-[#0a0a0a] border border-white/15 rounded-sm px-3 py-2 text-sm">
            <option value="">Giornata...</option>
            {matchdays.filter((n) => !takenMds.includes(n)).map((n) => <option key={n} value={n}>Giornata {n}</option>)}
          </select>
          <input data-testid="admin-tiket-deadline" type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} className="bg-[#0a0a0a] border border-white/15 rounded-sm px-3 py-2 text-sm" />
          <select data-testid="admin-tiket-bigmatch" value={bigMatch} onChange={(e) => setBigMatch(e.target.value)} disabled={!fixtures.length} className="bg-[#0a0a0a] border border-white/15 rounded-sm px-3 py-2 text-sm">
            {fixtures.map((f) => <option key={f.id} value={f.id}>{f.home_team} - {f.away_team}</option>)}
          </select>
        </div>
        <button data-testid="admin-tiket-create" onClick={create} disabled={!md} className="mt-3 bg-[#0057B8] hover:bg-[#00438F] disabled:opacity-40 text-white text-sm font-semibold rounded-sm px-4 py-2 transition-colors">Crea giornata</button>
      </div>

      {activeRound && activeRound.status !== "scored" && (
        <div>
          <div className="text-xs uppercase tracking-[0.2em] text-[#00FF66] mb-3">Inserisci risultati · Giornata {activeRound.matchday}</div>
          <div className="space-y-2">
            {activeRound.fixtures.map((f) => (
              <div key={f.id} className="flex items-center justify-between gap-3">
                <span className="text-sm flex-1">{f.home_team} - {f.away_team}</span>
                <Segmented value={results[f.id]} onChange={(v) => setResults((r) => ({ ...r, [f.id]: v }))} testidPrefix={`admin-tiket-res-${f.id}`} />
              </div>
            ))}
          </div>
          <button data-testid="admin-tiket-save-results" onClick={saveResults} className="mt-3 bg-[#00FF66] hover:bg-[#00cc52] text-black text-sm font-semibold rounded-sm px-4 py-2 flex items-center gap-2 transition-colors">
            <CheckCircle2 size={16} /> Calcola classifica
          </button>
        </div>
      )}
    </div>
  );
}
