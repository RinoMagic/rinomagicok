import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { SEASON } from "@/lib/constants";
import { Gift, Trash2, Target, Goal } from "lucide-react";

// Gestione Giochi Bonus: configura Big Match / primo marcatore e liquida i premi.
export default function GestioneBonus() {
  const [configs, setConfigs] = useState([]);
  const [type, setType] = useState("exact_score");
  const [md, setMd] = useState(1);
  const [fixtures, setFixtures] = useState([]);
  const [bigMatch, setBigMatch] = useState("");
  const [settleInputs, setSettleInputs] = useState({}); // cid -> {home, away, player}

  const load = async () => {
    try { setConfigs(await api("/bonus/configs")); } catch (e) { toast.error(e.message); }
  };
  useEffect(() => { load(); }, []);

  const loadFixtures = async (m) => {
    try {
      const r = await api(`/sal/calendar?season=${SEASON}&matchday=${m}`);
      setFixtures((r.fixtures || []).filter((f) => !f.excluded));
    } catch { setFixtures([]); }
  };
  useEffect(() => { if (type === "exact_score") loadFixtures(md); }, [md, type]);

  const create = async () => {
    try {
      const body = { season: SEASON, matchday: Number(md), bonus_type: type };
      if (type === "exact_score") {
        if (!bigMatch) { toast.error("Scegli il Big Match"); return; }
        const [home_team, away_team] = bigMatch.split("|||");
        body.big_match = { home_team, away_team };
      }
      await api("/bonus/configs", { method: "POST", body });
      toast.success("Bonus configurato");
      setBigMatch("");
      load();
    } catch (e) { toast.error(e.message); }
  };

  const del = async (c) => {
    if (!window.confirm("Eliminare questa configurazione bonus e i relativi pronostici?")) return;
    try { await api(`/bonus/configs/${c.id}`, { method: "DELETE" }); toast.success("Eliminato"); load(); }
    catch (e) { toast.error(e.message); }
  };

  const setIn = (cid, patch) => setSettleInputs((p) => ({ ...p, [cid]: { ...p[cid], ...patch } }));

  const settle = async (c) => {
    const v = settleInputs[c.id] || {};
    try {
      if (c.bonus_type === "exact_score") {
        const r = await api(`/bonus/configs/${c.id}/settle-exact`, { method: "POST", body: { home_score: Number(v.home || 0), away_score: Number(v.away || 0) } });
        toast.success(`Liquidato · ${r.winners} vincitori`);
      } else {
        if (!v.player) { toast.error("Inserisci il primo marcatore"); return; }
        const r = await api(`/bonus/configs/${c.id}/settle-scorer`, { method: "POST", body: { player_name: v.player } });
        toast.success(`Liquidato · ${r.winners} vincitori`);
      }
      load();
    } catch (e) { toast.error(e.message); }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-[#10B981]/40 bg-[#10B981]/5 p-4 space-y-3">
        <div className="font-bold flex items-center gap-2"><Gift size={18} className="text-[#10B981]" /> Nuovo bonus</div>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-[#94A3B8]">Tipo
            <select data-testid="gb-type" value={type} onChange={(e) => setType(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm">
              <option value="exact_score">Big Match (Tiket + Survival)</option>
              <option value="first_scorer">Primo Marcatore (Score + Fanta)</option>
            </select>
          </label>
          <label className="text-xs text-[#94A3B8]">Giornata
            <input data-testid="gb-md" type="number" min="1" max="38" value={md} onChange={(e) => setMd(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
          </label>
        </div>
        {type === "exact_score" && (
          <label className="text-xs text-[#94A3B8] block">Big Match
            <select data-testid="gb-bigmatch" value={bigMatch} onChange={(e) => setBigMatch(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm">
              <option value="">— scegli partita —</option>
              {fixtures.map((f) => <option key={f.id} value={`${f.home_team}|||${f.away_team}`}>{f.home_team} - {f.away_team}</option>)}
            </select>
          </label>
        )}
        <button data-testid="gb-create" onClick={create} className="bg-[#10B981] text-[#08110A] font-bold text-sm rounded-md px-4 py-2">Configura bonus</button>
      </div>

      <div className="space-y-3">
        <div className="font-bold text-sm text-[#94A3B8]">Bonus configurati</div>
        {configs.length === 0 && <div className="rounded-xl border border-white/10 bg-[#181D22] p-6 text-center text-[#94A3B8]">Nessun bonus configurato.</div>}
        {configs.map((c) => (
          <div key={c.id} data-testid={`gb-cfg-${c.id}`} className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-2">
            <div className="flex items-center gap-2">
              {c.bonus_type === "exact_score" ? <Target size={15} className="text-[#10B981]" /> : <Goal size={15} className="text-[#10B981]" />}
              <span className="font-bold flex-1">G{c.matchday} · {c.bonus_type === "exact_score" ? "Big Match" : "Primo Marcatore"}</span>
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${c.status === "settled" ? "bg-[#00D95F]/20 text-[#00D95F]" : c.status === "locked" ? "bg-[#F59E0B]/20 text-[#F59E0B]" : "bg-white/10 text-[#94A3B8]"}`}>{c.status?.toUpperCase()}</span>
              <button data-testid={`gb-del-${c.id}`} onClick={() => del(c)} className="p-1.5 text-[#EF4444] hover:bg-white/5 rounded"><Trash2 size={15} /></button>
            </div>
            {c.big_match && <div className="text-sm text-[#94A3B8]">{c.big_match.home_team} - {c.big_match.away_team}</div>}
            {c.status !== "settled" ? (
              c.bonus_type === "exact_score" ? (
                <div className="flex items-center gap-2">
                  <input data-testid={`gb-h-${c.id}`} type="number" min="0" placeholder="Casa" onChange={(e) => setIn(c.id, { home: e.target.value })} className="w-16 bg-[#0F1216] border border-white/15 rounded px-2 py-1.5 text-center text-sm" />
                  <span className="text-[#94A3B8]">-</span>
                  <input data-testid={`gb-a-${c.id}`} type="number" min="0" placeholder="Ospite" onChange={(e) => setIn(c.id, { away: e.target.value })} className="w-16 bg-[#0F1216] border border-white/15 rounded px-2 py-1.5 text-center text-sm" />
                  <button data-testid={`gb-settle-${c.id}`} onClick={() => settle(c)} className="ml-auto bg-[#10B981] text-[#08110A] font-bold text-sm rounded-md px-4 py-1.5">Liquida</button>
                </div>
              ) : (
                <div className="flex items-center gap-2">
                  <input data-testid={`gb-player-${c.id}`} placeholder="Primo marcatore" onChange={(e) => setIn(c.id, { player: e.target.value })} className="flex-1 bg-[#0F1216] border border-white/15 rounded px-3 py-1.5 text-sm" />
                  <button data-testid={`gb-settle-${c.id}`} onClick={() => settle(c)} className="bg-[#10B981] text-[#08110A] font-bold text-sm rounded-md px-4 py-1.5">Liquida</button>
                </div>
              )
            ) : (
              <div className="text-xs text-[#00D95F]">Risultato: {c.result ? JSON.stringify(c.result) : "—"}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
