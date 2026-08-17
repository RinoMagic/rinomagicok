import { useState } from "react";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { apiError } from "@/lib/api";
import { LogIn } from "lucide-react";

const BG = "https://images.pexels.com/photos/32190700/pexels-photo-32190700.jpeg";

export default function Login() {
  const { login } = useAuth();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (!identifier || !password) return;
    setLoading(true);
    try {
      const u = await login(identifier.trim(), password);
      toast.success(`Bentornato, ${u.nickname}!`);
    } catch (err) {
      toast.error(apiError(err, "Credenziali non valide"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen grid lg:grid-cols-2">
      <div
        className="hidden lg:block relative bg-cover bg-center"
        style={{ backgroundImage: `url(${BG})` }}
      >
        <div className="absolute inset-0 bg-black/70" />
        <div className="absolute bottom-12 left-12 right-12 z-10">
          <div className="font-display text-6xl text-white leading-none">
            IL BAR DELLE<br />SCHEDINE
          </div>
          <p className="mt-4 text-zinc-300 max-w-md">
            Tiket, Survival e i tuoi pronostici sulla Serie A. Sfida gli amici, scala la classifica.
          </p>
        </div>
      </div>

      <div className="flex items-center justify-center px-6 py-16 relative">
        <div className="w-full max-w-sm">
          <div className="flex items-center gap-3 mb-10">
            <img src="/icon-192.png" alt="logo" className="h-12 w-12 rounded-sm" />
            <div>
              <div className="font-display text-3xl leading-none">SCHEDINA BAR</div>
              <div className="text-[10px] tracking-[0.28em] text-[#00FF66] uppercase">Serie A 2026-27</div>
            </div>
          </div>

          <h1 className="font-display text-4xl mb-1">ACCEDI</h1>
          <p className="text-zinc-400 text-sm mb-8">Entra con username o email.</p>

          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="text-xs tracking-[0.2em] uppercase text-zinc-500">Username o Email</label>
              <input
                data-testid="login-identifier-input"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                autoCapitalize="none"
                autoCorrect="off"
                className="mt-2 w-full bg-[#141414] border border-white/10 rounded-sm px-4 py-3 text-white outline-none focus:ring-2 focus:ring-[#0057B8] transition-colors"
                placeholder="es. andr97"
              />
            </div>
            <div>
              <label className="text-xs tracking-[0.2em] uppercase text-zinc-500">Password</label>
              <input
                data-testid="login-password-input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-2 w-full bg-[#141414] border border-white/10 rounded-sm px-4 py-3 text-white outline-none focus:ring-2 focus:ring-[#0057B8] transition-colors"
                placeholder="••••••••"
              />
            </div>
            <button
              data-testid="login-submit-button"
              disabled={loading}
              className="w-full bg-[#0057B8] hover:bg-[#00438F] disabled:opacity-50 text-white font-semibold rounded-sm px-4 py-3 flex items-center justify-center gap-2 transition-colors duration-200"
            >
              <LogIn size={18} />
              {loading ? "Accesso..." : "Accedi"}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
