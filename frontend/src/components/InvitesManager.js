import { useEffect, useState, useCallback } from "react";
import { toast } from "sonner";
import { Ticket, Plus, Copy, Trash2, CheckCircle2, XCircle } from "lucide-react";
import { api } from "@/lib/api";

// Reusable admin invite manager. basePath e.g. "/rooms/{id}", "/sv/tournaments/{id}",
// "/sal/tournaments/{id}", "/fg/leagues/{id}". Invites live at `${basePath}/invites`.
export default function InvitesManager({ basePath }) {
  const [invites, setInvites] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try { setInvites(await api(`${basePath}/invites`)); }
    catch (e) { toast.error(e.message); }
    finally { setLoading(false); }
  }, [basePath]);
  useEffect(() => { load(); }, [load]);

  const create = async () => {
    try { await api(`${basePath}/invites`, { method: "POST", body: {} }); toast.success("Nuovo codice generato"); load(); }
    catch (e) { toast.error(e.message); }
  };
  const revoke = async (id) => {
    try { await api(`${basePath}/invites/${id}`, { method: "DELETE" }); toast.success("Codice revocato"); load(); }
    catch (e) { toast.error(e.message); }
  };

  return (
    <div className="rounded-xl border border-white/10 bg-[#181D22] p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="font-bold flex items-center gap-2"><Ticket size={18} className="text-[#F59E0B]" /> Gestione inviti</div>
        <button data-testid="inv-create" onClick={create} className="text-sm bg-[#F59E0B] text-[#1A1000] font-bold rounded-md px-3 py-1.5 flex items-center gap-1"><Plus size={15} /> Genera</button>
      </div>
      {loading ? <div className="text-sm text-[#94A3B8]">Caricamento...</div>
      : invites.length === 0 ? <div className="text-sm text-[#94A3B8]">Nessun invito. Genera un codice da condividere.</div>
      : (
        <div className="divide-y divide-white/10">
          {invites.map((inv) => {
            const used = !!inv.used_by_user_id;
            const revoked = !!inv.revoked_at;
            return (
              <div key={inv.id} data-testid={`inv-${inv.id}`} className="py-2 flex items-center gap-3">
                <span className={`font-mono font-bold tracking-widest ${revoked ? "line-through text-white/30" : used ? "text-[#94A3B8]" : "text-[#00D95F]"}`}>{inv.code}</span>
                <span className="text-xs flex items-center gap-1">
                  {revoked ? <><XCircle size={12} className="text-[#EF4444]" /> revocato</>
                  : used ? <><CheckCircle2 size={12} className="text-[#94A3B8]" /> usato da {inv.used_by_nickname || "?"}</>
                  : <span className="text-[#00D95F]">disponibile</span>}
                </span>
                <div className="ml-auto flex items-center gap-1">
                  {!used && !revoked && (
                    <>
                      <button data-testid={`inv-copy-${inv.id}`} onClick={() => { navigator.clipboard?.writeText(inv.code); toast.success("Copiato"); }} className="p-1.5 text-[#94A3B8] hover:text-white"><Copy size={15} /></button>
                      <button data-testid={`inv-revoke-${inv.id}`} onClick={() => revoke(inv.id)} className="p-1.5 text-[#EF4444] hover:brightness-125"><Trash2 size={15} /></button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
