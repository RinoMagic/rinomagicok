import { useState, useRef } from "react";
import { toast } from "sonner";
import { apiUpload } from "@/lib/api";
import { Users } from "lucide-react";

// Lista Calciatori: carica il Listone Fantacalcio (PDF o Excel) con anteprima.
export default function ListaCalciatori() {
  const [preview, setPreview] = useState(null);
  const [file, setFile] = useState(null);
  const [path, setPath] = useState(null);
  const [replaceAll, setReplaceAll] = useState(true);
  const [busy, setBusy] = useState(false);
  const pdfRef = useRef(null);
  const xlsxRef = useRef(null);

  const doPreview = async (ref, p) => {
    const f = ref.current?.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      const r = await apiUpload(p, f, { dry_run: true });
      setPreview({ extracted: r.extracted, by_team: r.by_team || {}, by_role: r.by_role || {}, sample: r.sample || [] });
      setFile(f); setPath(p);
      toast.success(`Anteprima: ${r.extracted} giocatori`);
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); if (ref.current) ref.current.value = ""; }
  };

  const confirmImport = async () => {
    if (!file || !path) return;
    setBusy(true);
    try {
      const r = await apiUpload(path, file, { dry_run: false, replace_all: replaceAll });
      toast.success(`Importati ${r.inserted} giocatori · totale ${r.total}`);
      setPreview(null); setFile(null); setPath(null);
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
        <div className="font-bold flex items-center gap-2"><Users size={18} className="text-[#8B5CF6]" /> Carica Listone (PDF o Excel)</div>
        <input ref={pdfRef} data-testid="lc-pdf" type="file" accept="application/pdf" onChange={() => doPreview(pdfRef, "/sal/players/import-pdf")} className="hidden" />
        <input ref={xlsxRef} data-testid="lc-xlsx" type="file" accept=".xlsx" onChange={() => doPreview(xlsxRef, "/sal/players/import-xlsx")} className="hidden" />
        <div className="flex gap-2 flex-wrap">
          <button onClick={() => pdfRef.current?.click()} disabled={busy} className="border border-white/15 rounded-md px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-50">Anteprima da PDF</button>
          <button onClick={() => xlsxRef.current?.click()} disabled={busy} className="border border-white/15 rounded-md px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-50">Anteprima da Excel</button>
        </div>
        {preview && (
          <div className="space-y-2">
            <div className="text-sm text-[#94A3B8]">{preview.extracted} giocatori rilevati · {Object.keys(preview.by_team).length} squadre</div>
            <div className="flex flex-wrap gap-1.5 text-xs">
              {Object.entries(preview.by_role).map(([r, n]) => <span key={r} className="px-2 py-0.5 rounded bg-[#8B5CF6]/15 text-[#8B5CF6]">{r}: {n}</span>)}
            </div>
            <div className="rounded-lg bg-[#0F1216] border border-white/10 max-h-44 overflow-y-auto text-sm divide-y divide-white/5">
              {preview.sample.map((p, i) => (
                <div key={i} className="px-3 py-1.5 flex gap-2"><span className="w-6 text-[#8B5CF6]">{p.role}</span><span className="flex-1 truncate">{p.first_name} {p.last_name}</span><span className="text-[#94A3B8]">{p.team}</span></div>
              ))}
            </div>
            <label className="flex items-center gap-2 text-sm text-[#94A3B8] cursor-pointer">
              <input data-testid="lc-replace" type="checkbox" checked={replaceAll} onChange={(e) => setReplaceAll(e.target.checked)} /> Sostituisci l'intero listone esistente
            </label>
            <div className="flex gap-2">
              <button data-testid="lc-confirm" onClick={confirmImport} disabled={busy} className="bg-[#00D95F] text-[#08110A] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Conferma import</button>
              <button onClick={() => { setPreview(null); setFile(null); }} className="border border-white/15 rounded-md px-4 py-2 text-sm">Annulla</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
