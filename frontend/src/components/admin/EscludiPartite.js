import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { SEASON } from "@/lib/constants";
import { Ban, RotateCcw, Trash2 } from "lucide-react";

// Escludi Partite: elenca le partite di una giornata dal calendario Serie A,
// permette di escluderle (spariscono da tutti i giochi) o eliminarle (rinvii).
export default function EscludiPartite() {
  const [md, setMd] = useState(1);
  const [fixtures, setFixtures] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setBusy(true);
    try {
      const r = await api(`/sal/calendar?season=${SEASON}&matchday=${md}`);
      setFixtures(r.fixtures || []);
      setLoaded(true);
      if (!(r.fixtures || []).length) toast.info("Nessuna partita in calendario per questa giornata");
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); }
  };

  const toggleExclude = async (fx) => {
    try {
      await api(`/sal/calendar/fixture/${fx.id}/exclude`, { method: "PATCH", body: { excluded: !fx.excluded } });
      toast.success(fx.excluded ? "Partita reintegrata" : "Partita esclusa da tutti i giochi");
      load();
    } catch (e) { toast.error(e.message); }
  };

  const del = async (fx) => {
    if (!window.confirm(`Eliminare ${fx.home_team} - ${fx.away_team} dal calendario?`)) return;
    try {
      await api(`/sal/calendar/fixture/${fx.id}`, { method: "DELETE" });
      toast.success("Partita eliminata");
      load();
    } catch (e) { toast.error(e.message); }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-[#EF4444]/40 bg-[#EF4444]/5 p-4 flex items-end gap-3">
        <label className="text-xs text-[#94A3B8] flex-1">Giornata
          <input data-testid="ep-md" type="number" min="1" max="38" value={md} onChange={(e) => setMd(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2" />
        </label>
        <button data-testid="ep-load" onClick={load} disabled={busy} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Carica partite</button>
      </div>

      {loaded && (
        <div className="space-y-2">
          {fixtures.length === 0 && <div className="rounded-xl border border-white/10 bg-[#181D22] p-6 text-center text-[#94A3B8]">Nessuna partita.</div>}
          {fixtures.map((fx) => (
            <div key={fx.id} data-testid={`ep-fx-${fx.id}`} className={`rounded-lg border p-3 flex items-center gap-2 ${fx.excluded ? "border-[#EF4444]/50 bg-[#EF4444]/10" : "border-white/10 bg-[#181D22]"}`}>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{fx.home_team} <span className="text-[#94A3B8]">vs</span> {fx.away_team}</div>
                {fx.excluded && <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#EF4444]/25 text-[#EF4444] font-bold">ESCLUSA</span>}
              </div>
              <button data-testid={`ep-toggle-${fx.id}`} onClick={() => toggleExclude(fx)} title={fx.excluded ? "Reintegra" : "Escludi"} className={`p-2 rounded-md ${fx.excluded ? "text-[#00D95F]" : "text-[#F59E0B]"} hover:bg-white/5`}>
                {fx.excluded ? <RotateCcw size={16} /> : <Ban size={16} />}
              </button>
              <button data-testid={`ep-del-${fx.id}`} onClick={() => del(fx)} title="Elimina (rinvio)" className="p-2 rounded-md text-[#EF4444] hover:bg-white/5"><Trash2 size={16} /></button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
