import { useState, useRef } from "react";
import { toast } from "sonner";
import { api, apiUpload } from "@/lib/api";
import { SEASON } from "@/lib/constants";
import { Trash2 } from "lucide-react";

// Calendario Serie A: carica PDF o Excel (anteprima + conferma) oppure
// inserisci/elimina partite manualmente.
export default function CalendarioSerieA() {
  const [preview, setPreview] = useState(null); // {path, params, sample, extracted, matchdays}
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState(null);
  const pdfRef = useRef(null);
  const xlsxRef = useRef(null);

  // manual add
  const [md, setMd] = useState(1);
  const [home, setHome] = useState("");
  const [away, setAway] = useState("");
  const [kickoff, setKickoff] = useState("");

  // list
  const [listMd, setListMd] = useState(1);
  const [fixtures, setFixtures] = useState(null);

  const doPreview = async (ref, path) => {
    const f = ref.current?.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      const r = await apiUpload(path, f, { season: SEASON, dry_run: true, replace: true });
      setPreview({ path, sample: r.sample || [], extracted: r.extracted, matchdays: r.matchdays || [] });
      setFile(f);
      toast.success(`Anteprima: ${r.extracted} partite`);
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); if (ref.current) ref.current.value = ""; }
  };

  const confirmImport = async () => {
    if (!preview || !file) return;
    setBusy(true);
    try {
      const r = await apiUpload(preview.path, file, { season: SEASON, dry_run: false, replace: true });
      toast.success(`Calendario importato: ${r.inserted} partite`);
      setPreview(null); setFile(null);
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); }
  };

  const addManual = async () => {
    if (!home.trim() || !away.trim()) { toast.error("Inserisci le due squadre"); return; }
    try {
      await api("/sal/calendar/import", { method: "POST", body: { season: SEASON, replace: false, fixtures: [{ matchday: Number(md), home_team: home.trim(), away_team: away.trim(), kickoff_iso: kickoff || null }] } });
      toast.success("Partita aggiunta");
      setHome(""); setAway(""); setKickoff("");
      if (fixtures && Number(listMd) === Number(md)) loadList();
    } catch (e) { toast.error(e.message); }
  };

  const loadList = async () => {
    try {
      const r = await api(`/sal/calendar?season=${SEASON}&matchday=${listMd}`);
      setFixtures(r.fixtures || []);
    } catch (e) { toast.error(e.message); }
  };

  const del = async (fx) => {
    if (!window.confirm(`Eliminare ${fx.home_team} - ${fx.away_team}?`)) return;
    try { await api(`/sal/calendar/fixture/${fx.id}`, { method: "DELETE" }); toast.success("Eliminata"); loadList(); }
    catch (e) { toast.error(e.message); }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
        <div className="font-bold">Carica calendario (PDF o Excel)</div>
        <p className="text-xs text-[#94A3B8]">Excel: colonne <b>Giornata</b>, <b>Casa</b>, <b>Trasferta</b> (e opzionale <b>Data</b>).</p>
        <input ref={pdfRef} data-testid="cal-pdf" type="file" accept="application/pdf" onChange={() => doPreview(pdfRef, "/sal/calendar/import-pdf")} className="hidden" />
        <input ref={xlsxRef} data-testid="cal-xlsx" type="file" accept=".xlsx" onChange={() => doPreview(xlsxRef, "/sal/calendar/import-xlsx")} className="hidden" />
        <div className="flex gap-2 flex-wrap">
          <button onClick={() => pdfRef.current?.click()} disabled={busy} className="border border-white/15 rounded-md px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-50">Anteprima da PDF</button>
          <button onClick={() => xlsxRef.current?.click()} disabled={busy} className="border border-white/15 rounded-md px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-50">Anteprima da Excel</button>
        </div>
        {preview && (
          <div className="space-y-2">
            <div className="text-sm text-[#94A3B8]">{preview.extracted} partite · giornate {preview.matchdays.join(", ")}</div>
            <div className="rounded-lg bg-[#0F1216] border border-white/10 max-h-48 overflow-y-auto text-sm divide-y divide-white/5">
              {preview.sample.map((fx, i) => (
                <div key={i} className="px-3 py-1.5 flex gap-2"><span className="text-[#F59E0B] w-8">G{fx.matchday}</span><span className="truncate">{fx.home_team} - {fx.away_team}</span></div>
              ))}
            </div>
            <div className="flex gap-2">
              <button data-testid="cal-confirm" onClick={confirmImport} disabled={busy} className="bg-[#00D95F] text-[#08110A] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Conferma import (sostituisce la stagione)</button>
              <button onClick={() => { setPreview(null); setFile(null); }} className="border border-white/15 rounded-md px-4 py-2 text-sm">Annulla</button>
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
        <div className="font-bold">Inserimento manuale</div>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-[#94A3B8]">Giornata<input data-testid="cal-md" type="number" min="1" max="38" value={md} onChange={(e) => setMd(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" /></label>
          <label className="text-xs text-[#94A3B8]">Kickoff (opz.)<input data-testid="cal-kick" type="datetime-local" value={kickoff} onChange={(e) => setKickoff(e.target.value ? new Date(e.target.value).toISOString() : "")} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" /></label>
          <input data-testid="cal-home" value={home} onChange={(e) => setHome(e.target.value)} placeholder="Squadra casa" className="bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
          <input data-testid="cal-away" value={away} onChange={(e) => setAway(e.target.value)} placeholder="Squadra trasferta" className="bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
        </div>
        <button data-testid="cal-add" onClick={addManual} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2">Aggiungi partita</button>
      </div>

      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
        <div className="flex items-end gap-2">
          <label className="text-xs text-[#94A3B8] flex-1">Visualizza giornata<input data-testid="cal-listmd" type="number" min="1" max="38" value={listMd} onChange={(e) => setListMd(e.target.value)} className="mt-1 w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" /></label>
          <button data-testid="cal-list" onClick={loadList} className="border border-white/15 rounded-md px-4 py-2 text-sm">Mostra</button>
        </div>
        {fixtures && (
          <div className="space-y-1.5">
            {fixtures.length === 0 && <div className="text-sm text-[#94A3B8] text-center py-3">Nessuna partita.</div>}
            {fixtures.map((fx) => (
              <div key={fx.id} className="rounded-md bg-[#0F1216] border border-white/10 px-3 py-2 flex items-center gap-2 text-sm">
                <span className="flex-1 truncate">{fx.home_team} - {fx.away_team}{fx.excluded && <span className="ml-2 text-[10px] text-[#EF4444]">ESCLUSA</span>}</span>
                <button data-testid={`cal-del-${fx.id}`} onClick={() => del(fx)} className="p-1.5 text-[#EF4444] hover:bg-white/5 rounded"><Trash2 size={15} /></button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
