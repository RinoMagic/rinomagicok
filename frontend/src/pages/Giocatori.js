import { useEffect, useState } from "react";
import api from "@/lib/api";
import { Search } from "lucide-react";

const ROLES = [
  { key: "", label: "Tutti" },
  { key: "P", label: "Portieri" },
  { key: "D", label: "Difensori" },
  { key: "C", label: "Centrocampisti" },
  { key: "A", label: "Attaccanti" },
];
const ROLE_COLOR = { P: "#E3A621", D: "#00FF66", C: "#0057B8", A: "#E32221" };

export default function Giocatori() {
  const [role, setRole] = useState("");
  const [search, setSearch] = useState("");
  const [data, setData] = useState({ total: 0, players: [] });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = setTimeout(() => {
      setLoading(true);
      api
        .get(`/players`, { params: { role: role || undefined, search: search || undefined, limit: 80 } })
        .then(({ data }) => setData(data))
        .finally(() => setLoading(false));
    }, 250);
    return () => clearTimeout(t);
  }, [role, search]);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-4xl">GIOCATORI</h1>
        <p className="text-zinc-400 text-sm">{data.total} calciatori quotati</p>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" size={18} />
        <input
          data-testid="players-search-input"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Cerca un giocatore..."
          className="w-full bg-[#141414] border border-white/10 rounded-sm pl-10 pr-4 py-3 outline-none focus:ring-2 focus:ring-[#0057B8] transition-colors"
        />
      </div>

      <div className="flex gap-2 overflow-x-auto pb-1">
        {ROLES.map((r) => (
          <button
            key={r.key || "all"}
            data-testid={`players-role-${r.key || "all"}`}
            onClick={() => setRole(r.key)}
            className={`shrink-0 px-4 py-2 rounded-sm border text-sm transition-colors duration-200 ${
              role === r.key ? "bg-white text-black border-white" : "bg-[#141414] border-white/10 text-zinc-400 hover:text-white"
            }`}
          >
            {r.label}
          </button>
        ))}
      </div>

      <div className="rounded-sm border border-white/10 bg-[#141414] divide-y divide-white/10">
        {loading ? (
          <div className="px-5 py-10 text-center text-zinc-500">Caricamento...</div>
        ) : (
          data.players.map((p) => (
            <div key={p.id} data-testid={`player-row-${p.id}`} className="px-5 py-3 flex items-center gap-3">
              <span
                className="h-8 w-8 rounded-sm flex items-center justify-center text-xs font-bold"
                style={{ backgroundColor: (ROLE_COLOR[p.role] || "#888") + "22", color: ROLE_COLOR[p.role] || "#888" }}
              >
                {p.role}
              </span>
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate">{p.full_name}</div>
                <div className="text-xs text-zinc-500">{p.team}</div>
              </div>
              <div className="font-display text-2xl text-[#00FF66]">{p.price_current}</div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
