import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

export default function Login() {
  const { loginAdmin, loginPlayer, register, forgot } = useAuth();
  const [mode, setMode] = useState("login"); // login | register | forgot
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [showPw, setShowPw] = useState(false);
  const [err, setErr] = useState(null);
  const [ok, setOk] = useState(null);

  const isEmail = (v) => v.includes("@");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr(null); setOk(null);
    try {
      const id = identifier.trim();
      if (mode === "login") {
        if (!id || !password) throw new Error("Inserisci email/nickname e password.");
        if (isEmail(id)) await loginAdmin(id, password);
        else await loginPlayer(id, password);
      } else if (mode === "register") {
        if (!id || !password) throw new Error("Inserisci nickname e password.");
        if (isEmail(id)) throw new Error("Il nickname non può contenere una @.");
        await register(id, password);
      } else {
        if (!id || !isEmail(id)) throw new Error("Inserisci una email valida.");
        const res = await forgot(id);
        setOk(res.message || "Se l'email è registrata, riceverai le istruzioni.");
      }
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  };

  const cta = mode === "login" ? "Accedi" : mode === "register" ? "Registrati" : "Invia link di reset";
  const placeholder = mode === "login" ? "Email o Nickname" : mode === "register" ? "Nickname (2-20 caratteri)" : "Email admin";
  const title = mode === "login" ? "Bentornato" : mode === "register" ? "Crea il tuo account" : "Recupero password";
  const sub = mode === "login" ? "Accedi con la tua email admin o il tuo nickname."
    : mode === "register" ? "Scegli un nickname e una password."
    : "Riceverai un link per reimpostare la password.";

  return (
    <div className="min-h-screen flex items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center gap-2 mb-6">
          <img src="/barslot-logo.jpg" alt="RinoMagic" className="w-56 h-20 object-contain rounded-md" />
          <div className="text-3xl font-black tracking-wider" style={{ textShadow: "0 2px 6px rgba(0,0,0,.6)" }}>RinoMagic</div>
          <div className="w-14 h-1 rounded bg-[#F59E0B]" />
        </div>

        <div className="rounded-2xl border border-white/12 bg-[#0F172A]/70 backdrop-blur-md p-6 shadow-2xl">
          <h1 className="text-xl font-extrabold text-center">{title}</h1>
          <p className="text-sm text-white/70 text-center mt-1 mb-4">{sub}</p>

          <form onSubmit={submit} className="space-y-3">
            <input
              data-testid="auth-identifier"
              value={identifier}
              onChange={(e) => setIdentifier(e.target.value)}
              placeholder={placeholder}
              autoCapitalize="none"
              className="w-full bg-[rgba(255,255,255,0.08)] border border-[rgba(255,255,255,0.18)] rounded-md px-4 py-3 text-white placeholder-[rgba(255,255,255,0.5)] outline-none focus:ring-2 focus:ring-[#F59E0B] transition-colors"
            />
            {mode !== "forgot" && (
              <div className="relative">
                <input
                  data-testid="auth-password"
                  type={showPw ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Password"
                  className="w-full bg-[rgba(255,255,255,0.08)] border border-[rgba(255,255,255,0.18)] rounded-md px-4 py-3 pr-12 text-white placeholder-[rgba(255,255,255,0.5)] outline-none focus:ring-2 focus:ring-[#F59E0B] transition-colors"
                />
                <button
                  type="button"
                  data-testid="toggle-password-visibility"
                  onClick={() => setShowPw((v) => !v)}
                  aria-label={showPw ? "Nascondi password" : "Mostra password"}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-white/60 hover:text-white transition-colors"
                >
                  {showPw ? <EyeOff size={20} /> : <Eye size={20} />}
                </button>
              </div>
            )}
            {err && <p className="text-[#ff6b6b] text-sm text-center">{err}</p>}
            {ok && <p className="text-[#4ade80] text-sm text-center">{ok}</p>}
            <button
              data-testid="auth-submit"
              disabled={busy}
              className="w-full h-12 rounded-lg bg-[#F59E0B] text-[#1A1000] font-extrabold tracking-wide disabled:opacity-60 hover:brightness-105 transition-all"
            >
              {busy ? "..." : cta}
            </button>
          </form>

          <div className="flex flex-wrap items-center justify-center gap-2 mt-4 text-sm font-bold">
            {mode === "login" && (
              <>
                <button data-testid="go-register" onClick={() => { setMode("register"); setErr(null); setOk(null); setPassword(""); }}>Registrati</button>
                <span className="text-white/40">•</span>
                <button data-testid="go-forgot" onClick={() => { setMode("forgot"); setErr(null); setOk(null); setPassword(""); }}>Password dimenticata?</button>
              </>
            )}
            {mode !== "login" && (
              <button data-testid="go-login" onClick={() => { setMode("login"); setErr(null); setOk(null); }}>← Torna al login</button>
            )}
          </div>
        </div>

        <p className="text-center text-white/55 text-xs italic mt-5">Chi ha la quota più bassa, paga da bere.</p>
      </div>
    </div>
  );
}
