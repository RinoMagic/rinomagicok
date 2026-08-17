import { useNavigate } from "react-router-dom";
import { Ticket, Skull, Trophy, Users, ChevronRight, Bell } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { enablePush, pushSupported } from "@/push";

const STADIUM = "https://images.pexels.com/photos/30651230/pexels-photo-30651230.jpeg";

function GameCard({ title, subtitle, icon: Icon, color, onClick, comingSoon, testid }) {
  return (
    <button
      data-testid={testid}
      onClick={comingSoon ? undefined : onClick}
      disabled={comingSoon}
      className={`group relative text-left overflow-hidden rounded-sm border border-white/10 bg-[#141414] p-5 min-h-[140px] flex flex-col justify-between transition-colors duration-200 ${
        comingSoon ? "opacity-60 cursor-not-allowed" : "hover:border-white/30"
      }`}
    >
      <div className="flex items-start justify-between">
        <div
          className="h-11 w-11 rounded-sm flex items-center justify-center"
          style={{ backgroundColor: color + "22", color }}
        >
          <Icon size={22} />
        </div>
        {comingSoon ? (
          <span className="text-[10px] tracking-[0.2em] uppercase bg-white/10 text-zinc-300 px-2 py-1 rounded-sm">
            Prossimamente
          </span>
        ) : (
          <ChevronRight className="text-zinc-600 group-hover:text-white transition-colors" size={20} />
        )}
      </div>
      <div>
        <div className="font-display text-3xl leading-none text-white">{title}</div>
        <div className="text-sm text-zinc-400 mt-1">{subtitle}</div>
      </div>
    </button>
  );
}

export default function Home() {
  const navigate = useNavigate();
  const { user } = useAuth();

  const doEnablePush = async () => {
    try {
      await enablePush();
      toast.success("Notifiche attivate!");
    } catch (e) {
      toast.error(e.message);
    }
  };

  return (
    <div className="space-y-8">
      <section
        className="relative overflow-hidden rounded-sm border border-white/10 bg-cover bg-center"
        style={{ backgroundImage: `url(${STADIUM})` }}
      >
        <div className="absolute inset-0 bg-black/75" />
        <div className="relative z-10 p-7 sm:p-10">
          <div className="text-[10px] tracking-[0.28em] uppercase text-[#00FF66]">Ciao {user?.nickname}</div>
          <h1 className="font-display text-4xl sm:text-6xl leading-none mt-2">SCEGLI IL TUO GIOCO</h1>
          <p className="text-zinc-300 mt-3 max-w-xl text-sm">
            Compila la Schedina Tiket, sopravvivi al Survival e sfida gli altri giocatori del bar.
          </p>
          {pushSupported() && (
            <button
              data-testid="enable-push-button"
              onClick={doEnablePush}
              className="mt-5 inline-flex items-center gap-2 bg-white/10 hover:bg-white/20 border border-white/15 text-white text-sm rounded-sm px-4 py-2 transition-colors duration-200"
            >
              <Bell size={16} /> Attiva notifiche
            </button>
          )}
        </div>
      </section>

      <section>
        <h2 className="font-display text-2xl mb-4 text-zinc-200">I GIOCHI</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <GameCard
            testid="game-tiket"
            title="TIKET"
            subtitle="Pronostici 1 · X · 2"
            icon={Ticket}
            color="#0057B8"
            onClick={() => navigate("/tiket")}
          />
          <GameCard
            testid="game-survival"
            title="SURVIVAL"
            subtitle="Sopravvivi ogni giornata"
            icon={Skull}
            color="#00FF66"
            onClick={() => navigate("/survival")}
          />
          <GameCard
            testid="game-scoreandlive"
            title="SCORE&LIVE"
            subtitle="In arrivo"
            icon={Trophy}
            color="#E32221"
            comingSoon
          />
          <GameCard
            testid="game-fantagiornata"
            title="FANTAGIORNATA"
            subtitle="In arrivo"
            icon={Users}
            color="#E3A621"
            comingSoon
          />
        </div>
      </section>

      <section className="grid grid-cols-2 gap-4">
        <button
          data-testid="home-calendario"
          onClick={() => navigate("/calendario")}
          className="rounded-sm border border-white/10 bg-[#141414] p-5 text-left hover:border-white/30 transition-colors duration-200"
        >
          <div className="font-display text-2xl">CALENDARIO</div>
          <div className="text-sm text-zinc-400">380 partite · 38 giornate</div>
        </button>
        <button
          data-testid="home-giocatori"
          onClick={() => navigate("/giocatori")}
          className="rounded-sm border border-white/10 bg-[#141414] p-5 text-left hover:border-white/30 transition-colors duration-200"
        >
          <div className="font-display text-2xl">GIOCATORI</div>
          <div className="text-sm text-zinc-400">497 calciatori quotati</div>
        </button>
      </section>
    </div>
  );
}
