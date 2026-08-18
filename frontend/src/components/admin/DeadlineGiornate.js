import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { SEASON } from "@/lib/constants";
import { Clock } from "lucide-react";

const toLocalInput = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  const off = d.getTimezoneOffset();
  return new Date(d.getTime() - off * 60000).toISOString().slice(0, 16);
};

// Deadline Giornate: timer di chiusura pronostici (calendario + orologio).
export default function DeadlineGiornate() {
  const [md, setMd] = useState(1);
  const [deadline, setDeadline] = useState("");
  const [list, setList] = useState([]);

  const load = async () => {
    try {
      const r = await api(`/deadlines?season=${SEASON}`);
      const arr = Array.isArray(r) ? r : (r.deadlines || []);
      setList(arr.filter((d) => d.deadline_at).sort((a, b) => a.matchday - b.matchday));
    } catch (e) { /* silent */ }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    try {
      const iso = deadline ? new Date(deadline).toISOString() : null;
      await api(`/deadlines/${md}?season=${encodeURIComponent(SEASON)}`, { method: "PUT", body: { deadline_at: iso } });
      toast.success(`Scadenza giornata ${md} salvata`);
      setDeadline("");
      load();
    } catch (e) { toast.error(e.message); }
  };

  const editRow = (d) => { setMd(d.matchday); setDeadline(toLocalInput(d.deadline_at)); };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-[#F59E0B]/40 bg-[#F59E0B]/5 p-4 space-y-3">
        <div className="font-bold flex items-center gap-2"><Clock size={18} className="text-[#F59E0B]" /> Imposta scadenza</div>
        <div className="grid sm:grid-cols-2 gap-3">
          <label className="text-xs text-[#94A3B8]">Giornata
            <input data-testid="dl-md" type="number" min="1" max="38" value={md} onChange={(e) => setMd(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2" />
          </label>
          <label className="text-xs text-[#94A3B8]">Data e ora chiusura
            <input data-testid="dl-datetime" type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2" />
          </label>
        </div>
        <button data-testid="dl-save" onClick={save} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2">Salva scadenza G{md}</button>
      </div>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-2">
        <div className="font-bold">Scadenze impostate</div>
        {list.length === 0 && <div className="text-sm text-[#94A3B8] text-center py-3">Nessuna scadenza impostata.</div>}
        {list.map((d) => (
          <button key={d.matchday} data-testid={`dl-row-${d.matchday}`} onClick={() => editRow(d)} className="w-full text-left rounded-md bg-[#0F1216] border border-white/10 px-3 py-2 flex items-center justify-between hover:border-[#F59E0B]/50 transition-colors">
            <span className="font-bold text-[#F59E0B]">Giornata {d.matchday}</span>
            <span className="text-sm text-[#94A3B8]">{d.deadline_at ? new Date(d.deadline_at).toLocaleString("it-IT") : "—"}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
