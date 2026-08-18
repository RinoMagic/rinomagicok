import { useState, useRef } from "react";
import { toast } from "sonner";
import { apiUpload, api } from "@/lib/api";
import { SEASON } from "@/lib/constants";

// Calcola Giornata: carica Excel voti → anteprima modificabile risultati +
// rinvii → conferma → liquida tutti i giochi in un colpo.
export default function CalcolaGiornata() {
  const [md, setMd] = useState(1);
  const [file, setFile] = useState(null);
  const [votiPreview, setVotiPreview] = useState(null);
  const [imported, setImported] = useState(false);
  const [preview, setPreview] = useState(null);
  const [edits, setEdits] = useState({});         // key -> {home, away}
  const [postponed, setPostponed] = useState({}); // key -> bool
  const [fsName, setFsName] = useState("");
  const [fsTeam, setFsTeam] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const xlsxRef = useRef(null);

  const key = (fx) => `${fx.home_team}||${fx.away_team}`;

  const onSelect = async () => {
    const f = xlsxRef.current?.files?.[0];
    if (!f) return;
    setBusy(true); setResult(null); setPreview(null); setImported(false);
    try {
      const r = await apiUpload("/admin/voti/upload-xlsx", f, { dry_run: true, matchday_override: md });
      setFile(f);
      setVotiPreview({ matchday: r.matchday, players: r.players ?? r.rows?.length, rows: r.rows || [] });
      toast.success(`Anteprima voti: giornata ${r.matchday}`);
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); if (xlsxRef.current) xlsxRef.current.value = ""; }
  };

  const importVoti = async () => {
    if (!file) return;
    setBusy(true);
    try {
      await apiUpload("/admin/voti/upload-xlsx", file, { dry_run: false, replace: true, matchday_override: md });
      setImported(true);
      toast.success(`Voti giornata ${md} importati`);
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); }
  };

  const calcPreview = async () => {
    setBusy(true); setResult(null);
    try {
      const r = await api("/admin/settle-matchday/preview", { method: "POST", body: { matchday: Number(md), season: SEASON } });
      setPreview(r);
      const e0 = {}; const p0 = {};
      (r.fixtures?.list || []).forEach((fx) => {
        e0[key(fx)] = { home: fx.home_score ?? "", away: fx.away_score ?? "" };
        p0[key(fx)] = !!fx.postponed;
      });
      setEdits(e0); setPostponed(p0);
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); }
  };

  const setScore = (k, side, val) => setEdits((p) => ({ ...p, [k]: { ...p[k], [side]: val } }));
  const togglePost = (k) => setPostponed((p) => ({ ...p, [k]: !p[k] }));

  const publish = async () => {
    if (!window.confirm(`Pubblicare i risultati e liquidare TUTTI i giochi della giornata ${md}? L'operazione aggiorna vite, punti e classifiche.`)) return;
    setBusy(true);
    try {
      const list = preview.fixtures?.list || [];
      const postponed_matches = [];
      const fixture_overrides = [];
      list.forEach((fx) => {
        const k = key(fx);
        if (postponed[k]) {
          postponed_matches.push({ home_team: fx.home_team, away_team: fx.away_team });
        } else {
          const e = edits[k] || {};
          if (e.home !== "" && e.away !== "" && e.home != null && e.away != null) {
            fixture_overrides.push({ home_team: fx.home_team, away_team: fx.away_team, home_score: Number(e.home), away_score: Number(e.away) });
          }
        }
      });
      const body = { matchday: Number(md), season: SEASON, fixture_overrides, postponed_matches };
      if (fsName.trim()) { body.first_scorer_player_name = fsName.trim(); body.first_scorer_team = fsTeam.trim() || null; }
      const r = await api("/admin/settle-matchday/commit", { method: "POST", body });
      setResult(r);
      toast.success("Giornata liquidata!");
    } catch (e) { toast.error(e.message); }
    finally { setBusy(false); }
  };

  const fixtures = preview?.fixtures?.list || [];

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-[#10B981]/40 bg-[#10B981]/5 p-4 space-y-3">
        <label className="text-xs text-[#94A3B8]">Giornata da calcolare</label>
        <input data-testid="cg-md" type="number" min="1" max="38" value={md} onChange={(e) => setMd(e.target.value)} className="w-full bg-[#0F1216] border border-white/15 rounded-md px-3 py-2" />
      </div>

      {/* Step 1: Excel voti */}
      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
        <div className="font-bold">1. Carica Excel Voti</div>
        <input ref={xlsxRef} data-testid="cg-xlsx" type="file" accept=".xlsx" onChange={onSelect} className="hidden" />
        <div className="flex gap-2 flex-wrap">
          <button onClick={() => xlsxRef.current?.click()} disabled={busy} className="border border-white/15 rounded-md px-4 py-2 text-sm hover:bg-white/5 disabled:opacity-50">Scegli file .xlsx</button>
          {votiPreview && !imported && <button data-testid="cg-import" onClick={importVoti} disabled={busy} className="bg-[#F59E0B] text-[#1A1000] font-bold text-sm rounded-md px-4 py-2 disabled:opacity-50">Importa voti G{md}</button>}
        </div>
        {votiPreview && <div className="text-sm text-[#94A3B8]">Rilevata giornata <b className="text-white">{votiPreview.matchday}</b> · {votiPreview.players} giocatori. {imported ? "✓ Importati" : "Premi «Importa voti» per salvare."}</div>}
      </div>

      {/* Step 2: preview + edit */}
      <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
        <div className="flex items-center justify-between">
          <div className="font-bold">2. Anteprima risultati</div>
          <button data-testid="cg-calc" onClick={calcPreview} disabled={busy || !imported} className="border border-white/15 rounded-md px-4 py-2 text-sm disabled:opacity-50">Calcola anteprima</button>
        </div>
        {!imported && <p className="text-xs text-[#94A3B8]">Importa prima i voti.</p>}
        {preview && (
          <>
            {(preview.warnings || []).length > 0 && (
              <div className="rounded-md bg-[#F59E0B]/10 border border-[#F59E0B]/30 p-2 text-xs text-[#F59E0B] space-y-1">
                {preview.warnings.map((w, i) => <div key={i}>⚠ {w}</div>)}
              </div>
            )}
            <div className="space-y-2">
              {fixtures.map((fx) => {
                const k = key(fx);
                const isP = postponed[k];
                return (
                  <div key={k} data-testid={`cg-fx-${k}`} className={`rounded-lg border p-2.5 ${isP ? "border-[#EF4444]/40 bg-[#EF4444]/5" : "border-white/10 bg-[#0F1216]"}`}>
                    <div className="flex items-center gap-2 text-sm">
                      <span className="flex-1 truncate">{fx.home_team} <span className="text-[#94A3B8]">vs</span> {fx.away_team}</span>
                      <input data-testid={`cg-h-${k}`} type="number" min="0" disabled={isP} value={edits[k]?.home ?? ""} onChange={(e) => setScore(k, "home", e.target.value)} className="w-12 bg-[#181D22] border border-white/15 rounded px-2 py-1 text-center disabled:opacity-40" />
                      <span className="text-[#94A3B8]">-</span>
                      <input data-testid={`cg-a-${k}`} type="number" min="0" disabled={isP} value={edits[k]?.away ?? ""} onChange={(e) => setScore(k, "away", e.target.value)} className="w-12 bg-[#181D22] border border-white/15 rounded px-2 py-1 text-center disabled:opacity-40" />
                      <label className="flex items-center gap-1 text-xs text-[#94A3B8] cursor-pointer ml-1">
                        <input data-testid={`cg-post-${k}`} type="checkbox" checked={isP} onChange={() => togglePost(k)} /> Rinviata
                      </label>
                    </div>
                  </div>
                );
              })}
            </div>
            {preview.first_scorer_bonus_open && (
              <div className="rounded-lg border border-[#10B981]/30 bg-[#10B981]/5 p-3 space-y-2">
                <div className="text-sm font-bold text-[#10B981]">Bonus Primo Marcatore attivo</div>
                <div className="flex gap-2">
                  <input data-testid="cg-fs-name" value={fsName} onChange={(e) => setFsName(e.target.value)} placeholder="Nome primo marcatore" className="flex-1 bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
                  <input data-testid="cg-fs-team" value={fsTeam} onChange={(e) => setFsTeam(e.target.value)} placeholder="Squadra (opz.)" className="w-32 bg-[#0F1216] border border-white/15 rounded-md px-3 py-2 text-sm" />
                </div>
              </div>
            )}
            <button data-testid="cg-publish" onClick={publish} disabled={busy} className="w-full bg-[#EF4444] text-white font-bold rounded-md py-3 disabled:opacity-50">Pubblica risultati e liquida G{md}</button>
          </>
        )}
      </div>

      {result && (
        <div className="rounded-xl border border-[#00D95F]/40 bg-[#00D95F]/5 p-4 space-y-2">
          <div className="font-bold text-[#00D95F]">Liquidazione completata</div>
          <pre className="text-xs bg-[#0F1216] rounded-md p-3 overflow-auto max-h-60 text-[#94A3B8]">{JSON.stringify(result.summary, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
