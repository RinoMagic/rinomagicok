import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronLeft, Gift, Target, Goal } from "lucide-react";
import { api } from "@/lib/api";

const SEASON = "2026-27";
const TABS = [
  { game: "tiket", label: "Tiket", type: "exact_score" },
  { game: "survival", label: "Survival", type: "exact_score" },
  { game: "score", label: "Score", type: "first_scorer" },
  { game: "fanta", label: "Fanta", type: "first_scorer" },
];

export default function Bonus() {
  const navigate = useNavigate();
  const [tab, setTab] = useState("survival");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [inputs, setInputs] = useState({}); // subId -> {home,away} or {player}

  const active = TABS.find((t) => t.game === tab);

  const load = useCallback(async () => {
    setLoading(true); setData(null);
    try { setData(await api(`/bonus/available?game=${tab}&season=${SEASON}`)); }
    catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, [tab]);
  useEffect(() => { load(); }, [load]);

  const submit = async (sub) => {
    const v = inputs[sub.id] || {};
    try {
      if (active.type === "exact_score") {
        await api("/bonus/picks/exact", { method: "POST", body: { game: tab, season: SEASON, subscription_id: sub.id, home_score: Number(v.home || 0), away_score: Number(v.away || 0) } });
      } else {
        if (!v.player) throw new Error("Inserisci il nome del marcatore");
        await api("/bonus/picks/scorer", { method: "POST", body: { game: tab, season: SEASON, subscription_id: sub.id, player_name: v.player } });
      }
      toast.success("Pronostico bonus salvato!");
      load();
    } catch (e) { toast.error(e.message); }
  };

  const setIn = (id, patch) => setInputs((p) => ({ ...p, [id]: { ...p[id], ...patch } }));

  const cfg = data?.config;
  const subs = data?.subscriptions || [];

  return (
    <div className="space-y-5">
      <button data-testid="bonus-back" onClick={() => navigate("/")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Hub</button>
      <div>
        <h1 className="text-2xl font-extrabold flex items-center gap-2"><Gift className="text-[#10B981]" /> Giochi Bonus</h1>
        <p className="text-[#94A3B8] text-sm">Un bonus a giornata per ogni gioco: vinci vite o punti extra.</p>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        {TABS.map((t) => (
          <button key={t.game} data-testid={`bonus-tab-${t.game}`} onClick={() => setTab(t.game)}
            className={`shrink-0 px-4 py-2 rounded-md text-sm font-bold transition-colors ${tab === t.game ? "bg-[#10B981] text-[#08110A]" : "bg-[#181D22] border border-white/10 text-[#94A3B8]"}`}>
            {t.label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="py-12 text-center text-[#94A3B8]">Caricamento...</div>
      ) : !data?.eligible ? (
        <div className="rounded-xl border border-white/10 bg-[#181D22] p-8 text-center text-[#94A3B8]">Non sei iscritto a nessuna stanza/torneo di {active.label}: iscriviti per giocare il bonus.</div>
      ) : !cfg ? (
        <div className="rounded-xl border border-white/10 bg-[#181D22] p-8 text-center text-[#94A3B8]">Nessun bonus attivo al momento per {active.label}.</div>
      ) : (
        <div className="space-y-4">
          <div className="rounded-xl border border-[#10B981]/40 bg-[#10B981]/5 p-4">
            <div className="text-xs uppercase tracking-widest text-[#10B981] flex items-center gap-1">
              {active.type === "exact_score" ? <Target size={13} /> : <Goal size={13} />}
              {active.type === "exact_score" ? "Big Match — risultato esatto" : "Primo marcatore"}
            </div>
            {cfg.big_match && <div className="text-lg font-extrabold mt-1">{cfg.big_match.home_team} - {cfg.big_match.away_team}</div>}
            <div className="text-sm text-[#94A3B8]">Giornata {cfg.matchday}</div>
          </div>

          {subs.map((s) => (
            <div key={s.id} data-testid={`bonus-sub-${s.id}`} className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-2">
              <div className="text-sm font-bold">{s.name || s.room_name || s.tournament_name || "La tua iscrizione"}</div>
              {s.my_pick && <div className="text-xs text-[#00D95F]">Pronostico attuale: {JSON.stringify(s.my_pick.pick || s.my_pick)}</div>}
              {active.type === "exact_score" ? (
                <div className="flex items-center gap-2">
                  <input data-testid={`bonus-home-${s.id}`} type="number" min="0" placeholder="Casa" onChange={(e) => setIn(s.id, { home: e.target.value })} className="w-20 bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-center" />
                  <span className="text-[#94A3B8]">-</span>
                  <input data-testid={`bonus-away-${s.id}`} type="number" min="0" placeholder="Ospite" onChange={(e) => setIn(s.id, { away: e.target.value })} className="w-20 bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-center" />
                  <button data-testid={`bonus-save-${s.id}`} onClick={() => submit(s)} className="ml-auto bg-[#10B981] text-[#08110A] font-bold text-sm rounded-md px-4 py-2">Salva</button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <input data-testid={`bonus-player-${s.id}`} placeholder="Nome marcatore" onChange={(e) => setIn(s.id, { player: e.target.value })} className="flex-1 bg-[#0F1216] border border-white/15 rounded-md px-3 py-2" />
                  <button data-testid={`bonus-save-${s.id}`} onClick={() => submit(s)} className="bg-[#10B981] text-[#08110A] font-bold text-sm rounded-md px-4 py-2">Salva</button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
