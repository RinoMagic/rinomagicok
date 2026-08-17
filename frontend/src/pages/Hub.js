import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Trophy, Activity, Zap, Heart, Gift, ChevronRight, Info, Users } from "lucide-react";
import { api } from "@/lib/api";

const ICONS = { trophy: Trophy, pulse: Activity, football: Zap, heart: Heart };

export default function Hub() {
  const navigate = useNavigate();
  const [games, setGames] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api("/games").then(setGames).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const openGame = (g) => {
    if (!g.enabled) {
      toast.info(`${g.name} — Prossimamente`, { description: "Questo gioco sarà disponibile a breve." });
      return;
    }
    if (g.id === "thebesttiket") navigate("/tiket");
    else if (g.id === "surviva") navigate("/survival");
    else toast.info(`${g.name} — Prossimamente`);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-extrabold">Scegli il gioco</h1>
        <p className="text-[#94A3B8] text-sm mt-1">Sfida i tuoi amici in 4+1 giochi diversi con lo stesso account.</p>
      </div>

      {loading ? (
        <div className="py-16 text-center text-[#94A3B8]">Caricamento...</div>
      ) : (
        <div className="space-y-4">
          {games.map((g) => {
            const Icon = ICONS[g.icon] || Trophy;
            return (
              <button
                key={g.id}
                data-testid={`game-card-${g.id}`}
                onClick={() => openGame(g)}
                className="w-full flex items-center gap-4 p-4 rounded-2xl border text-left transition-transform hover:scale-[0.995]"
                style={{ borderColor: g.color, backgroundColor: g.color + "18", opacity: g.enabled ? 1 : 0.7 }}
              >
                <div className="w-14 h-14 rounded-xl flex items-center justify-center shrink-0" style={{ backgroundColor: g.color }}>
                  <Icon size={28} color="#1A1000" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-lg font-extrabold">{g.name}</span>
                    {!g.enabled && (
                      <span className="text-[9px] font-extrabold tracking-widest px-2 py-0.5 rounded-full bg-white/15 text-[#94A3B8]">PROSSIMAMENTE</span>
                    )}
                  </div>
                  <div className="text-sm text-white/80">{g.tagline}</div>
                  {g.enabled && (
                    <div className="flex items-center gap-1.5 mt-1.5 text-xs font-bold" style={{ color: g.color }}>
                      <Users size={13} />
                      {g.my_rooms_count === 0 ? "Nessuna stanza ancora" : `${g.my_rooms_count} ${g.my_rooms_count === 1 ? "stanza" : "stanze"}`}
                    </div>
                  )}
                </div>
                {g.enabled ? <ChevronRight size={22} style={{ color: g.color }} /> : <Info size={22} style={{ color: g.color }} />}
              </button>
            );
          })}

          <button
            data-testid="game-card-bonus"
            onClick={() => toast.info("Giochi Bonus — Prossimamente sul web")}
            className="w-full flex items-center gap-4 p-4 rounded-2xl border border-[#10B981] bg-[#10B98112] text-left transition-transform hover:scale-[0.995]"
          >
            <div className="w-14 h-14 rounded-xl flex items-center justify-center bg-[#10B981] shrink-0">
              <Gift size={28} color="#fff" />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <span className="text-lg font-extrabold">Giochi Bonus</span>
                <span className="text-[9px] font-extrabold tracking-widest px-2 py-0.5 rounded-full bg-[#10B981] text-[#1A1000]">NUOVO</span>
              </div>
              <div className="text-sm text-white/80">4 bonus a giornata: vinci vite, punti extra e giocate</div>
            </div>
            <ChevronRight size={22} className="text-white/70" />
          </button>
        </div>
      )}

      <div className="flex items-center gap-2 p-3 rounded-lg bg-[#181D22] text-[#94A3B8] text-xs">
        <Info size={16} />
        Ogni codice invito è valido solo per il gioco per cui è stato generato.
      </div>
    </div>
  );
}
