import { useEffect, useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ChevronLeft, Clock, Bell, FileText, Gavel } from "lucide-react";
import { api, apiUpload } from "@/lib/api";

const SEASON = "2026-27";

export default function Admin() {
  const navigate = useNavigate();
  const [md, setMd] = useState(1);
  const [deadline, setDeadline] = useState("");
  const [current, setCurrent] = useState(null);
  const [title, setTitle] = useState("");
  const [bodyMsg, setBodyMsg] = useState("");
  const [settleState, setSettleState] = useState(null);
  const [busy, setBusy] = useState(false);
  const pdfRef = useRef(null);
  const xlsxRef = useRef(null);

  const loadCurrent = () => api("/deadlines/current", { }).then(setCurrent).catch(() => {});
  useEffect(() => { loadCurrent(); }, []);

  const saveDeadline = async () => {
    try {
      const iso = deadline ? new Date(deadline).toISOString() : null;
      await api(`/deadlines/${md}`, { method: "PUT", body: { deadline_at: iso } });
      toast.success(`Scadenza giornata ${md} salvata`);
      loadCurrent();
    } catch (e) { toast.error(e.message); }
  };

  const broadcast = async () => {
    try {
      const r = await api("/push/broadcast", { method: "POST", body: { title, body: bodyMsg, url: "/" } });
      toast.success(`Inviata a ${r.sent ?? 0} dispositivi`);
      setTitle(""); setBodyMsg("");
    } catch (e) { toast.error(e.message); }
  };

  const upload = async (ref, path, label) => {
    const f = ref.current?.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      const r = await apiUpload(path, f, { matchday: md, season: SEASON });
      toast.success(`${label} importato (giornata ${md})`);
      console.log(r);
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); if (ref.current) ref.current.value = ""; }
  };

  const loadSettleState = async () => {
    try { setSettleState(await api(`/admin/settle-matchday/state?matchday=${md}&season=${SEASON}`)); }
    catch (e) { toast.error(e.message); }
  };
  const commitSettle = async () => {
    if (!window.confirm(`Liquidare la giornata ${md}? L'operazione aggiorna vite, punti e classifiche.`)) return;
    try {
      const r = await api("/admin/settle-matchday/commit", { method: "POST", body: { matchday: Number(md), season: SEASON } });
      toast.success("Giornata liquidata!");
      console.log(r);
      loadSettleState();
    } catch (e) { toast.error(e.message); }
  };

  const Card = ({ icon: Icon, title: t, children }) => (
    <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
      <div className="font-bold flex items-center gap-2"><Icon size={18} className="text-[#F59E0B]" /> {t}</div>
      {children}
    </div>
  );

  return (
    <div className="space-y-5">
      <button data-testid="admin-back" onClick={() => navigate("/")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Hub</button>
      <h1 className="text-2xl font-extrabold">Pannello Admin</h1>

      <div className="rounded-xl border border-[#F59E0B]/40 bg-[#F59E0B]/5 p-4">
        <label className="text-xs text-[#94A3B8]">Giornata di lavoro (stagione {SEASON})</label>
        <input data-testid="admin-md" type="number" min="1" max="38" value={md} onChange={(e) => setMd(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2" />
        {current && <p className="text-xs text-[#94A3B8] mt-2">Giornata corrente rilevata: {current.current_matchday ?? current.matchday ?? "-"}</p>}
      </div>

      <Card icon={Clock} title="Scadenze giornata">
        <input data-testid="admin-deadline" type="datetime-local" value={deadline} onChange={(e) => setDeadline(e.target.value)} className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
        <button data-testid="admin-deadline-save" onClick={saveDeadline} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2">Salva scadenza G{md}</button>
      </Card>

      <Card icon={Bell} title="Notifica broadcast">
        <input data-testid="admin-bc-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Titolo" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
        <input data-testid="admin-bc-body" value={bodyMsg} onChange={(e) => setBodyMsg(e.target.value)} placeholder="Messaggio" className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
        <button data-testid="admin-bc-send" onClick={broadcast} disabled={!title || !bodyMsg} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Invia a tutti</button>
      </Card>

      <Card icon={FileText} title="Import Voti (PDF / Excel)">
        <div className="flex gap-2 flex-wrap">
          <input ref={pdfRef} data-testid="admin-pdf" type="file" accept="application/pdf" onChange={() => upload(pdfRef, "/admin/voti/upload-pdf", "PDF voti")} className="hidden" />
          <button onClick={() => pdfRef.current?.click()} disabled={busy} className="border border-white/15 rounded-md px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-50">Carica PDF voti</button>
          <input ref={xlsxRef} data-testid="admin-xlsx" type="file" accept=".xlsx" onChange={() => upload(xlsxRef, "/admin/voti/upload-xlsx", "Excel voti")} className="hidden" />
          <button onClick={() => xlsxRef.current?.click()} disabled={busy} className="border border-white/15 rounded-md px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-50">Carica Excel voti</button>
        </div>
      </Card>

      <Card icon={Gavel} title="Liquidazione risultati">
        <div className="flex gap-2">
          <button data-testid="admin-settle-state" onClick={loadSettleState} className="border border-white/15 rounded-md px-4 py-2 text-sm">Verifica stato G{md}</button>
          <button data-testid="admin-settle-commit" onClick={commitSettle} className="bg-[#EF4444] text-white font-bold text-sm rounded-md px-4 py-2">Liquida G{md}</button>
        </div>
        {settleState && (
          <pre className="text-xs bg-[#0F1216] rounded-md p-3 overflow-auto max-h-40 text-[#94A3B8]">{JSON.stringify(settleState, null, 2)}</pre>
        )}
      </Card>
    </div>
  );
}
