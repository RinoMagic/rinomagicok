import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Gift, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { SEASON } from "@/lib/constants";

// Shows a call-to-action when there is an active bonus for `game` that the
// user is eligible for and has NOT yet played on at least one subscription.
export const BonusBanner = ({ game }) => {
  const navigate = useNavigate();
  const [show, setShow] = useState(false);
  const [label, setLabel] = useState("");

  useEffect(() => {
    let alive = true;
    api(`/bonus/available?game=${game}&season=${SEASON}`)
      .then((d) => {
        if (!alive) return;
        if (!d?.eligible || !d?.config) return;
        const subs = d.subscriptions || [];
        const anyToPlay = subs.some((s) => !s.my_pick);
        if (!anyToPlay) return;
        const t = d.bonus_type === "exact_score" ? "Big Match — risultato esatto" : "Primo marcatore";
        setLabel(`${t} · Giornata ${d.config.matchday}`);
        setShow(true);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [game]);

  if (!show) return null;
  return (
    <button
      data-testid={`bonus-banner-${game}`}
      onClick={() => navigate("/bonus")}
      className="w-full text-left rounded-xl border border-[#10B981]/50 bg-gradient-to-r from-[#10B981]/20 to-[#10B981]/5 p-4 flex items-center gap-3 hover:border-[#10B981] transition-colors animate-fadeup"
    >
      <div className="w-11 h-11 rounded-lg bg-[#10B981]/25 flex items-center justify-center shrink-0">
        <Gift size={22} className="text-[#10B981]" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-extrabold text-[#10B981]">Gioca il tuo Bonus!</div>
        <div className="text-sm text-[#94A3B8] truncate">{label}</div>
      </div>
      <ChevronRight size={20} className="text-[#10B981] shrink-0" />
    </button>
  );
};
