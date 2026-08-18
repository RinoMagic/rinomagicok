import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Bell } from "lucide-react";

// Notifiche: broadcast push + promemoria automatici prima della scadenza.
export default function Notifiche() {
  const [title, setTitle] = useState("");
  const [bodyMsg, setBodyMsg] = useState("");
  const [reminderOffsets, setReminderOffsets] = useState([]);

  useEffect(() => { api("/settings/reminders").then((r) => setReminderOffsets(r.offsets_minutes || [])).catch(() => {}); }, []);

  const broadcast = async () => {
    try {
      const r = await api("/push/broadcast", { method: "POST", body: { title, body: bodyMsg, url: "/" } });
      toast.success(`Inviata a ${r.sent ?? 0} dispositivi`);
      setTitle(""); setBodyMsg("");
    } catch (e) { toast.error(e.message); }
  };

  const toggleOffset = (m) => setReminderOffsets((p) => p.includes(m) ? p.filter((x) => x !== m) : [...p, m]);
  const saveReminders = async () => {
    try { const r = await api("/settings/reminders", { method: "PUT", body: { offsets_minutes: reminderOffsets } }); setReminderOffsets(r.offsets_minutes || []); toast.success("Promemoria salvati"); }
    catch (e) { toast.error(e.message); }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
        <div className="font-bold flex items-center gap-2"><Bell size={18} className="text-[#F59E0B]" /> Notifica broadcast</div>
        <input data-testid="nt-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Titolo" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
        <input data-testid="nt-body" value={bodyMsg} onChange={(e) => setBodyMsg(e.target.value)} placeholder="Messaggio" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
        <button data-testid="nt-send" onClick={broadcast} disabled={!title || !bodyMsg} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Invia a tutti</button>
      </div>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
        <div className="font-bold flex items-center gap-2"><Bell size={18} className="text-[#F59E0B]" /> Promemoria automatici</div>
        <p className="text-xs text-[#94A3B8]">Quando avvisare i partecipanti prima della chiusura dei pronostici.</p>
        <div className="flex flex-wrap gap-2">
          {[[1440, "24 ore"], [720, "12 ore"], [360, "6 ore"], [180, "3 ore"], [60, "1 ora"], [30, "30 min"]].map(([m, l]) => {
            const on = reminderOffsets.includes(m);
            return <button key={m} data-testid={`nt-reminder-${m}`} onClick={() => toggleOffset(m)} className={`px-3 py-1.5 rounded-md text-sm font-bold border transition-colors ${on ? "bg-[#F59E0B] text-[#1A1000] border-[#F59E0B]" : "bg-[#0F1216] text-[#94A3B8] border-white/15"}`}>{l}</button>;
          })}
        </div>
        <button data-testid="nt-reminder-save" onClick={saveReminders} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 w-fit">Salva promemoria</button>
      </div>
    </div>
  );
}
