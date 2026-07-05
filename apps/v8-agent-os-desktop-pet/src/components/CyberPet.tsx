import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { motion } from "motion/react";
import { Mic, MicOff, MonitorCog, MousePointer2, Power, Radio, Volume2, VolumeX } from "lucide-react";
import type { ChatMessage, DesktopConversationSummary, PetEmotion, PetSettings } from "../types";

type CyberPetProps = {
  connected: boolean;
  status: string;
  emotion: PetEmotion;
  settings: PetSettings;
  messages: ChatMessage[];
  activeConversation: DesktopConversationSummary | null;
  isListening: boolean;
  isSpeaking: boolean;
  clickThrough: boolean;
  onOpenAdmin: () => void;
  onOpenSettings: () => void;
  onToggleClickThrough: () => void;
  onToggleMuted: () => void;
  onToggleListening: () => void;
  onQuit: () => void;
};

const EMOTION_LABEL: Record<PetEmotion, string> = {
  idle: "待机",
  talking: "播报",
  listening: "聆听",
  curious: "观察",
  scanning: "分析",
  happy: "完成",
  worried: "异常",
  resting: "休息",
  thinking: "思考",
  tool_calling: "工具",
};

function glowColor(settings: PetSettings, emotion: PetEmotion) {
  if (settings.customGlowColor && settings.customGlowColor !== "default") {
    const map: Record<string, string> = {
      neon_blue: "#22d3ee",
      emerald_green: "#34d399",
      crimson_red: "#fb7185",
      cyber_purple: "#a78bfa",
      golden_amber: "#f59e0b",
    };
    return map[settings.customGlowColor] || "#22d3ee";
  }
  if (emotion === "worried") return "#fb7185";
  if (emotion === "happy") return "#f59e0b";
  if (emotion === "tool_calling") return "#34d399";
  if (emotion === "thinking") return "#a78bfa";
  return "#22d3ee";
}

function trimText(value: string, max = 90) {
  const text = value.replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

export default function CyberPet({
  connected,
  status,
  emotion,
  settings,
  messages,
  activeConversation,
  isListening,
  isSpeaking,
  clickThrough,
  onOpenAdmin,
  onOpenSettings,
  onToggleClickThrough,
  onToggleMuted,
  onToggleListening,
  onQuit,
}: CyberPetProps) {
  const petRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef({ dragging: false, startX: 0, startY: 0, x: 36, y: 36 });
  const [position, setPosition] = useState({ x: 36, y: 36 });
  const [menuOpen, setMenuOpen] = useState(false);
  const [floatOffset, setFloatOffset] = useState(0);
  const color = glowColor(settings, emotion);
  const scale = Math.max(0.45, Math.min(2, settings.petScale || 0.7));

  useEffect(() => {
    let frame = 0;
    let raf = 0;
    const animate = () => {
      frame += 0.018 * (settings.floatSpeed || 1);
      setFloatOffset(Math.sin(frame) * (settings.floatAmplitude || 8));
      raf = requestAnimationFrame(animate);
    };
    raf = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(raf);
  }, [settings.floatAmplitude, settings.floatSpeed]);

  useEffect(() => {
    const handleMove = (event: MouseEvent) => {
      if (!dragRef.current.dragging) return;
      const next = {
        x: dragRef.current.x + event.clientX - dragRef.current.startX,
        y: dragRef.current.y + event.clientY - dragRef.current.startY,
      };
      setPosition(next);
    };
    const handleUp = () => {
      dragRef.current.dragging = false;
      dragRef.current.x = position.x;
      dragRef.current.y = position.y;
    };
    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
  }, [position.x, position.y]);

  useEffect(() => {
    const onClick = () => setMenuOpen(false);
    window.addEventListener("click", onClick);
    return () => window.removeEventListener("click", onClick);
  }, []);

  const latestMessage = useMemo(() => messages[messages.length - 1], [messages]);
  const pulse = isListening || isSpeaking || emotion === "tool_calling" || emotion === "thinking";

  return (
    <div className="fixed inset-0 pointer-events-none select-none">
      <motion.div
        ref={petRef}
        className="pointer-events-auto fixed"
        style={{
          left: position.x,
          top: position.y + floatOffset,
          width: 280 * scale,
          height: 280 * scale,
        }}
        onMouseDown={(event) => {
          if (event.button !== 0) return;
          dragRef.current = {
            dragging: true,
            startX: event.clientX,
            startY: event.clientY,
            x: position.x,
            y: position.y,
          };
        }}
        onContextMenu={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setMenuOpen(true);
        }}
      >
        <div className="relative h-full w-full">
          <div
            className="absolute inset-[14%] rounded-full blur-2xl"
            style={{
              background: color,
              opacity: pulse ? 0.34 : 0.18,
              boxShadow: `0 0 ${pulse ? 78 : 48}px ${color}`,
            }}
          />
          <motion.div
            className="absolute inset-[18%] rounded-full border border-white/15 bg-slate-950/80 shadow-2xl backdrop-blur"
            animate={{ scale: pulse ? [1, 1.025, 1] : 1 }}
            transition={{ repeat: pulse ? Infinity : 0, duration: 1.4 }}
            style={{
              boxShadow: `inset 0 0 34px rgba(255,255,255,.08), 0 0 34px ${color}`,
            }}
          >
            <div
              className="absolute inset-[10%] rounded-full border"
              style={{ borderColor: `${color}88`, boxShadow: `0 0 18px ${color}` }}
            />
            <div className="absolute inset-[23%] rounded-full bg-black shadow-inner">
              <motion.div
                className="absolute left-1/2 top-1/2 h-[46%] w-[46%] -translate-x-1/2 -translate-y-1/2 rounded-full"
                animate={{
                  scale: isListening ? [1, 0.9, 1.08, 1] : isSpeaking ? [1, 1.14, 0.96, 1] : 1,
                }}
                transition={{ repeat: isListening || isSpeaking ? Infinity : 0, duration: 0.8 }}
                style={{
                  background: `radial-gradient(circle at 35% 32%, #fff 0 12%, ${color} 18%, #020617 66%)`,
                  boxShadow: `0 0 22px ${color}`,
                }}
              />
            </div>
            <div
              className="absolute bottom-[13%] left-1/2 h-1.5 w-[42%] -translate-x-1/2 rounded-full"
              style={{ background: `linear-gradient(90deg, transparent, ${color}, transparent)` }}
            />
          </motion.div>

          <div className="absolute left-1/2 top-[82%] min-w-[190px] -translate-x-1/2 rounded-2xl border border-white/10 bg-slate-950/80 px-3 py-2 text-center text-[11px] text-slate-200 shadow-xl backdrop-blur">
            <div className="flex items-center justify-center gap-2 font-semibold">
              <span className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-400" : "bg-amber-400"}`} />
              <span>{status}</span>
              <span className="text-slate-400">·</span>
              <span>{EMOTION_LABEL[emotion]}</span>
            </div>
            {activeConversation ? (
              <div className="mt-1 truncate text-slate-400">{trimText(activeConversation.title, 34)}</div>
            ) : null}
          </div>

          {latestMessage ? (
            <div className="absolute left-[76%] top-[16%] w-56 rounded-2xl border border-white/10 bg-slate-950/85 px-3 py-2 text-xs leading-5 text-slate-100 shadow-2xl backdrop-blur">
              <div className="mb-1 text-[10px] uppercase tracking-[0.18em] text-slate-500">
                {latestMessage.sender === "user" ? "Voice" : latestMessage.sender === "system" ? "System" : "V8OS"}
              </div>
              {trimText(latestMessage.text, 120)}
            </div>
          ) : null}

          {menuOpen ? (
            <div
              className="absolute left-[70%] top-[54%] z-50 w-56 overflow-hidden rounded-2xl border border-white/10 bg-slate-950/95 p-1 text-sm text-slate-100 shadow-2xl backdrop-blur-xl"
              onMouseDown={(event) => event.stopPropagation()}
              onClick={(event) => event.stopPropagation()}
            >
              <MenuButton icon={<MonitorCog size={16} />} label="打开 V8OS" onClick={onOpenAdmin} />
              <MenuButton icon={<MonitorCog size={16} />} label="打开桌宠设置" onClick={onOpenSettings} />
              <MenuButton icon={<Radio size={16} />} label={isListening ? "停止监听" : "开始监听"} onClick={onToggleListening} />
              <MenuButton icon={settings.muted ? <VolumeX size={16} /> : <Volume2 size={16} />} label={settings.muted ? "开启播报" : "静音播报"} onClick={onToggleMuted} />
              <MenuButton icon={<MousePointer2 size={16} />} label={clickThrough ? "关闭点击穿透" : "开启点击穿透"} onClick={onToggleClickThrough} />
              <div className="my-1 h-px bg-white/10" />
              <MenuButton icon={<Power size={16} />} label="关闭桌宠" danger onClick={onQuit} />
            </div>
          ) : null}

          <button
            type="button"
            className="absolute bottom-[10%] right-[10%] flex h-10 w-10 items-center justify-center rounded-full border border-white/10 bg-slate-950/80 text-slate-100 shadow-xl backdrop-blur transition hover:bg-slate-800"
            onClick={(event) => {
              event.stopPropagation();
              onToggleListening();
            }}
            title={isListening ? "停止监听" : "开始监听"}
          >
            {isListening ? <MicOff size={18} className="text-rose-300" /> : <Mic size={18} className="text-cyan-200" />}
          </button>
        </div>
      </motion.div>
    </div>
  );
}

function MenuButton({
  icon,
  label,
  danger,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left transition ${
        danger ? "text-rose-200 hover:bg-rose-500/15" : "text-slate-100 hover:bg-white/10"
      }`}
      onClick={onClick}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
