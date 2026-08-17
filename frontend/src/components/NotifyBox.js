import { useState } from "react";
import { toast } from "sonner";
import { Bell, Send } from "lucide-react";
import { api } from "@/lib/api";

// Admin-only targeted push. `userIds` = participant user ids; `url` = deep link.
export default function NotifyBox({ userIds = [], url = "/hub", label = "Notifica ai partecipanti" }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async () => {
    if (!title || !body) return;
    setBusy(true);
    try {
      const ids = [...new Set(userIds.filter(Boolean))];
      const r = await api("/push/broadcast", { method: "POST", body: { title, body, url, user_ids: ids } });
      toast.success(`Notifica inviata (${r.sent ?? 0} dispositivi)`);
      setTitle(""); setBody(""); setOpen(false);
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="rounded-xl border border-white/10 bg-[#181D22] p-4">
      <button data-testid="notify-toggle" onClick={() => setOpen((v) => !v)} className="w-full flex items-center gap-2 font-bold text-[#F59E0B]">
        <Bell size={18} /> {label} {userIds.length > 0 && <span className="text-xs text-[#94A3B8] font-normal">({userIds.length})</span>}
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          <input data-testid="notify-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Titolo" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
          <input data-testid="notify-body" value={body} onChange={(e) => setBody(e.target.value)} placeholder="Messaggio" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
          <button data-testid="notify-send" onClick={send} disabled={busy || !title || !body} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 flex items-center gap-2 disabled:opacity-50">
            <Send size={15} /> Invia
          </button>
        </div>
      )}
    </div>
  );
}
