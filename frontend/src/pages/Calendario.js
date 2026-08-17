import { useEffect, useState } from "react";
import api from "@/lib/api";

export default function Calendario() {
  const [matchdays, setMatchdays] = useState([]);
  const [md, setMd] = useState(1);
  const [matches, setMatches] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get("/serie-a/matchdays").then(({ data }) => setMatchdays(data.matchdays)).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    api
      .get(`/serie-a/calendar?matchday=${md}`)
      .then(({ data }) => setMatches(data.matches))
      .finally(() => setLoading(false));
  }, [md]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-4xl">CALENDARIO</h1>
        <p className="text-zinc-400 text-sm">Serie A 2026-27</p>
      </div>

      <div className="flex gap-2 overflow-x-auto pb-2 -mx-1 px-1">
        {matchdays.map((n) => (
          <button
            key={n}
            data-testid={`calendar-md-${n}`}
            onClick={() => setMd(n)}
            className={`shrink-0 h-10 min-w-10 px-3 rounded-sm border text-sm font-semibold transition-colors duration-200 ${
              md === n
                ? "bg-[#0057B8] border-[#0057B8] text-white"
                : "bg-[#141414] border-white/10 text-zinc-400 hover:text-white"
            }`}
          >
            {n}
          </button>
        ))}
      </div>

      <div className="rounded-sm border border-white/10 bg-[#141414] divide-y divide-white/10">
        <div className="px-5 py-3 text-xs tracking-[0.2em] uppercase text-zinc-500">Giornata {md}</div>
        {loading ? (
          <div className="px-5 py-10 text-center text-zinc-500">Caricamento...</div>
        ) : (
          matches.map((m) => (
            <div key={m.id} data-testid={`calendar-match-${m.id}`} className="px-5 py-4 flex items-center justify-between">
              <div className="flex-1 text-right pr-3 font-medium">{m.home_team}</div>
              <div className="text-[#00FF66] font-display text-lg px-2">VS</div>
              <div className="flex-1 pl-3 font-medium">{m.away_team}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
