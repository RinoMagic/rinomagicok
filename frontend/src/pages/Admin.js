import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronLeft, ChevronRight, Calculator, Ban, CalendarDays, Clock, Users, Gift, ShieldCheck, Bell, UserCog } from "lucide-react";
import { SEASON } from "@/lib/constants";
import CalcolaGiornata from "@/components/admin/CalcolaGiornata";
import EscludiPartite from "@/components/admin/EscludiPartite";
import CalendarioSerieA from "@/components/admin/CalendarioSerieA";
import DeadlineGiornate from "@/components/admin/DeadlineGiornate";
import ListaCalciatori from "@/components/admin/ListaCalciatori";
import GestioneBonus from "@/components/admin/GestioneBonus";
import GestioneAdmin from "@/components/admin/GestioneAdmin";
import Notifiche from "@/components/admin/Notifiche";
import GestioneUtenti from "@/components/admin/GestioneUtenti";

const TOOLS = [
  { id: "calcola", title: "Calcola Giornata", desc: "Carica Excel voti e liquida tutti i giochi in un solo passaggio", icon: Calculator, color: "#10B981", C: CalcolaGiornata },
  { id: "escludi", title: "Escludi Partite", desc: "Escludi partite pre-turno · gestione rinvii · sparisce da tutti i giochi", icon: Ban, color: "#EF4444", C: EscludiPartite },
  { id: "calendario", title: "Calendario Serie A", desc: "Carica il PDF/Excel del calendario o inserisci le partite manualmente", icon: CalendarDays, color: "#3B82F6", C: CalendarioSerieA },
  { id: "deadline", title: "Deadline Giornate", desc: "Timer di chiusura pronostici · vale per tutti i giochi", icon: Clock, color: "#F59E0B", C: DeadlineGiornate },
  { id: "lista", title: "Lista Calciatori", desc: "Carica il Listone Fantacalcio (PDF/Excel) — richiesto per picks e settlement", icon: Users, color: "#8B5CF6", C: ListaCalciatori },
  { id: "bonus", title: "Gestione Giochi Bonus", desc: "Configura Big Match, primo marcatore e liquida i premi", icon: Gift, color: "#F59E0B", C: GestioneBonus },
  { id: "admin", title: "Gestione Admin", desc: "Crea o rimuovi altri amministratori", icon: ShieldCheck, color: "#EC4899", C: GestioneAdmin },
  { id: "notifiche", title: "Notifiche", desc: "Invia notifiche push e configura i promemoria automatici", icon: Bell, color: "#F59E0B", C: Notifiche },
  { id: "utenti", title: "Gestione Utenti", desc: "Blocca, resetta la password o elimina i giocatori", icon: UserCog, color: "#22D3EE", C: GestioneUtenti },
];

export default function Admin() {
  const navigate = useNavigate();
  const [tool, setTool] = useState(null);
  const active = TOOLS.find((t) => t.id === tool);

  return (
    <div className="space-y-5">
      {active ? (
        <>
          <button data-testid="admin-tool-back" onClick={() => setTool(null)} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Strumenti Admin</button>
          <div className="flex items-center gap-3">
            <div className="w-11 h-11 rounded-xl flex items-center justify-center" style={{ backgroundColor: `${active.color}22` }}>
              <active.icon size={22} style={{ color: active.color }} />
            </div>
            <h1 className="text-2xl font-extrabold">{active.title}</h1>
          </div>
          <active.C />
        </>
      ) : (
        <>
          <button data-testid="admin-back" onClick={() => navigate("/")} className="flex items-center gap-1 text-[#94A3B8] hover:text-white text-sm transition-colors"><ChevronLeft size={16} /> Hub</button>
          <div>
            <h1 className="text-2xl font-extrabold">Pannello Admin</h1>
            <p className="text-[#94A3B8] text-sm">Stagione {SEASON}</p>
          </div>
          <div className="text-xs uppercase tracking-widest text-[#94A3B8] font-bold pt-1">Strumenti Admin</div>
          <div className="space-y-3">
            {TOOLS.map((t) => (
              <button key={t.id} data-testid={`admin-tool-${t.id}`} onClick={() => setTool(t.id)} className="w-full text-left rounded-xl border border-white/10 bg-[#181D22] p-4 flex items-center gap-4 hover:border-white/25 transition-colors">
                <div className="w-12 h-12 rounded-xl flex items-center justify-center shrink-0" style={{ backgroundColor: `${t.color}22` }}>
                  <t.icon size={24} style={{ color: t.color }} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-extrabold">{t.title}</div>
                  <div className="text-sm text-[#94A3B8]">{t.desc}</div>
                </div>
                <ChevronRight size={20} className="text-[#94A3B8] shrink-0" />
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
