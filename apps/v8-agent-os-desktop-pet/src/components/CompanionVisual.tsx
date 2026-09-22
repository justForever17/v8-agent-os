import React from 'react';
import type { PetEmotion } from '../types';
import { skinManifest } from '../lib/companion-skins';

export type CompanionVisualProps = {
  skinId?: string;
  isExiting?: boolean;
  emotion: PetEmotion;
  isTalking: boolean;
  audioVolume: number;
  glowIntensity: number;
  theme: { bgGlow: string; accentRing: string; glow: string; outerRing: string; pupil: string };
  mouseOffset: { x: number; y: number };
  pupilRadius: number;
  syncScalar: number;
  isWebcamActive: boolean;
  pupilCanvasRef: React.RefObject<HTMLCanvasElement | null>;
  isBlinking: boolean;
};
// This is a presentation contract: no credentials, transcript, tool arguments,
// Engine client or IPC bridge is passed to a skin. Window/menu actions stay in
// the trusted companion controller.
export default function CompanionVisual(props: CompanionVisualProps) {
  const { skinId, isExiting, emotion, isTalking, audioVolume, glowIntensity, theme,
    mouseOffset, pupilRadius, syncScalar, isWebcamActive, pupilCanvasRef, isBlinking } = props;
  const manifest = skinManifest(skinId);
  if (manifest.renderer === 'image') {
    return <div className={`relative w-48 h-48 flex items-center justify-center ${isExiting ? 'crt-exit' : 'crt-enter'}`}
      data-companion-skin={manifest.id} data-emotion={emotion}>
      <img src={manifest.asset} alt={manifest.name}
        draggable={false} className="w-44 h-44 pointer-events-none"
        style={{ transform: `scale(${isTalking ? Math.min(1.12, 1 + audioVolume / 900) : 1})`,
          filter: `drop-shadow(0 0 ${8 + glowIntensity * 18}px ${theme.accentRing})`,
          transition: 'transform 120ms ease-out' }} />
    </div>;
  }
  return (
      <div className={`relative w-48 h-48 select-none flex items-center justify-center ${isExiting ? 'crt-exit' : 'crt-enter'} ${emotion === 'worried' ? 'animate-jitter' : ''}`}>

        {/* Orbital Halo Flare outer boundary */}
        <div
          className={`absolute inset-4 rounded-full bg-transparent ${theme.bgGlow} pointer-events-none transition-all duration-300`}
          style={{
            opacity: glowIntensity,
            boxShadow: `0 0 ${18 + glowIntensity * 34}px ${theme.accentRing}`,
          }}
        />

        {/* Ambient Ring Waveform and stats text indicator */}
        <div className="absolute inset-0 border border-slate-900/60 rounded-full flex items-center justify-center pointer-events-none">
          <svg className="w-full h-full absolute scale-[1.05] overflow-visible" viewBox="0 0 200 200">
            {/* Fine outer radar graduation */}
            <circle cx="100" cy="100" r="95" fill="none" stroke="#1e293b" strokeWidth="0.8" strokeDasharray="2, 4" className="opacity-80" />

            {/* Interactive speech volumetric outer rings */}
            {isTalking && (
              <circle
                cx="100"
                cy="100"
                r={86 + (audioVolume / 6.5)}
                fill="none"
                stroke={theme.accentRing}
                strokeWidth="1.2"
                strokeOpacity="0.5"
                className="transition-all duration-75"
              />
            )}
          </svg>
        </div>

        {/* High-Tech Vector Canvas SVG representing Core-01 eye mechanism */}
        <svg id="orbital-eye-lens" className="w-[176px] h-[176px] drop-shadow-[0_10px_15px_rgba(0,0,0,0.8)]" viewBox="0 0 200 200">

          {/* Outermost dark metallic core frame */}
          <circle cx="100" cy="100" r="90" fill="#020617" stroke="#1e293b" strokeWidth="4.5" />

          {/* Atmospheric Tech ticks around grid inside bezel */}
          <circle cx="100" cy="100" r="82" fill="none" stroke="#334155" strokeWidth="1" strokeDasharray="1, 8" />

          {/* Fairy (ZZZ AI) fine coordinates and tracking crosshairs */}
          <line x1="25" y1="100" x2="175" y2="100" stroke={theme.accentRing} strokeWidth="0.8" strokeOpacity="0.4" strokeDasharray="6, 4" />
          <line x1="100" y1="25" x2="100" y2="175" stroke={theme.accentRing} strokeWidth="0.8" strokeOpacity="0.4" strokeDasharray="6, 4" />

          {/* Real electronic corner tracking brackets */}
          <path d="M 64 48 L 48 48 L 48 64" fill="none" stroke={theme.accentRing} strokeWidth="1.2" strokeOpacity="0.75" />
          <path d="M 136 48 L 152 48 L 152 64" fill="none" stroke={theme.accentRing} strokeWidth="1.2" strokeOpacity="0.75" />
          <path d="M 64 152 L 48 152 L 48 136" fill="none" stroke={theme.accentRing} strokeWidth="1.2" strokeOpacity="0.75" />
          <path d="M 136 152 L 152 152 L 152 136" fill="none" stroke={theme.accentRing} strokeWidth="1.2" strokeOpacity="0.75" />

          {/* Deep reflective glass base glow sphere */}
          <circle
            cx="100"
            cy="100"
            r="78"
            fill="radial-gradient(circle, #0f172a 35%, #020617 100%) animate-[pulse_3s_infinite]"
            stroke={theme.glow}
            strokeWidth="3.5"
            strokeOpacity="0.9"
            style={{
              filter: `drop-shadow(0px 0px 8px ${theme.glow})`
            }}
          />

          {/* Concentric rotating indicators */}
          <circle
            cx="100"
            cy="100"
            r="70"
            fill="none"
            stroke={theme.accentRing}
            strokeWidth="3.5"
            strokeDasharray="14, 5, 2, 5"
            className={`origin-center ${
              emotion === 'scanning'
                ? 'animate-[spin_1.5s_linear_infinite_reverse]'
                : emotion === 'talking'
                ? 'animate-[spin_2s_linear_infinite]'
                : emotion === 'tool_calling'
                ? 'animate-[spin_1s_linear_infinite]'
                : emotion === 'thinking'
                ? 'animate-[pulse_1s_infinite]'
                : 'animate-[spin_18s_linear_infinite]'
            }`}
          />

          {/* Animated blinking inner core - outer structure kept stable to avoid scaling artifacts */}
          <g>
            <circle cx="100" cy="100" r="60" fill="none" stroke={theme.outerRing} strokeWidth="8" />
            <circle cx="100" cy="100" r="50" fill="none" stroke="#cbd5e1" strokeWidth="8" />
            <circle cx="100" cy="100" r="42" fill="none" stroke={theme.pupil} strokeWidth="13" />

            {/* Interactive lens Pupil & reflections */}
            <g>
              <circle
                cx={100 + mouseOffset.x}
                cy={100 + mouseOffset.y}
                r={pupilRadius}
                fill="#06122d"
                stroke={theme.accentRing}
                strokeWidth="2.5"
                className="transition-all duration-300"
                style={{
                  transform: `scale(${syncScalar})`,
                  transformOrigin: `${100 + mouseOffset.x}px ${100 + mouseOffset.y}px`
                }}
              />

              {isWebcamActive && (
                <foreignObject
                  x={100 + mouseOffset.x - pupilRadius}
                  y={100 + mouseOffset.y - pupilRadius}
                  width={pupilRadius * 2}
                  height={pupilRadius * 2}
                  className="pointer-events-none"
                  style={{
                    transform: `scale(${syncScalar})`,
                    transformOrigin: `${100 + mouseOffset.x}px ${100 + mouseOffset.y}px`
                  }}
                >
                  <canvas
                    ref={pupilCanvasRef}
                    className="w-full h-full object-cover scale-x-[-1] pointer-events-none"
                    style={{
                      borderRadius: '50%',
                      opacity: 0.15,
                      mixBlendMode: 'screen',
                      filter: 'grayscale(1) brightness(1.8) contrast(1.5) sepia(0.3) hue-rotate(140deg)'
                    }}
                  />
                </foreignObject>
              )}

              {/* Simulated specular glare reflection */}
              <circle
                cx={112 + mouseOffset.x * 1.25}
                cy={112 + mouseOffset.y * 1.25}
                r="7.5"
                fill="#ffffff"
                fillOpacity="0.95"
                className="transition-all duration-300"
                style={{
                  filter: 'drop-shadow(0px 0px 4px rgba(255,255,255,0.95))'
                }}
              />

              {/* Ocular accent circle */}
              <circle
                cx={90 + mouseOffset.x}
                cy={90 + mouseOffset.y}
                r="3"
                fill="#e2e8f0"
                fillOpacity="0.4"
              />
            </g>
          </g>

          {/* Ocular Eyelid Overlay for clean electronic blinking without color-distortion line artifacts */}
          <circle
            cx="100"
            cy="100"
            r="76"
            fill="#020617"
            className="pointer-events-none"
            style={{
              transform: `scaleY(${isBlinking ? 1 : 0})`,
              transformOrigin: '100px 100px',
              transition: 'transform 80ms cubic-bezier(0.25, 1, 0.5, 1)',
              opacity: isBlinking ? 1 : 0
            }}
          />

          {/* Active Visor Laser swept lines */}
          {emotion === 'scanning' && (
            <g>
              <line
                x1="40"
                y1="100"
                x2="160"
                y2="100"
                stroke="#ef4444"
                strokeWidth="2.5"
                className="animate-[bounce_2s_infinite]"
                style={{
                  filter: 'drop-shadow(0px 0px 5px #ef4444)'
                }}
              />
              <circle cx="100" cy="100" r="35" fill="none" stroke="#ef4444" strokeWidth="1" strokeDasharray="4 4" className="animate-ping" />
            </g>
          )}
        </svg>

      </div>);
}
