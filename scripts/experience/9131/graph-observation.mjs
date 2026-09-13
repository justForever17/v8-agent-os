// Transparent observation of real Canvas/RAF/observer calls, not replacement rendering.
export function installGraphicsProbe() {
  const records = new WeakMap();
  const samples = [];
  const recentFrames = [];
  const activeRaf = new Set();
  const observerSets = { ResizeObserver: new Set(), IntersectionObserver: new Set(), MutationObserver: new Set() };
  let frames = 0;
  const originalClear = CanvasRenderingContext2D.prototype.clearRect;
  const originalArc = CanvasRenderingContext2D.prototype.arc;
  CanvasRenderingContext2D.prototype.clearRect = function (...args) {
    if (this.canvas.getAttribute('role') === 'img') {
      const previous = records.get(this.canvas);
      if (previous?.circles.length) { recentFrames.push(previous); if (recentFrames.length > 180) recentFrames.shift(); }
      records.set(this.canvas, { at: performance.now(), circles: [], frame: ++frames });
    }
    return originalClear.apply(this, args);
  };
  CanvasRenderingContext2D.prototype.arc = function (...args) {
    const record = records.get(this.canvas);
    if (record && record.circles.length < 2048) record.circles.push({ x: args[0], y: args[1], r: args[2] });
    return originalArc.apply(this, args);
  };
  const request = window.requestAnimationFrame.bind(window);
  const cancel = window.cancelAnimationFrame.bind(window);
  window.requestAnimationFrame = callback => {
    const id = request(time => {
      activeRaf.delete(id); const start = performance.now(), before = frames;
      try { return callback(time); }
      finally {
        if (frames !== before) { samples.push(performance.now() - start); if (samples.length > 3000) samples.shift(); }
      }
    }); activeRaf.add(id); return id;
  };
  window.cancelAnimationFrame = id => { activeRaf.delete(id); return cancel(id); };
  for (const name of Object.keys(observerSets)) {
    const Original = window[name];
    window[name] = class extends Original {
      observe(...args) { observerSets[name].add(this); return super.observe(...args); }
      disconnect() { observerSets[name].delete(this); return super.disconnect(); }
    };
  }
  window.__experienceGraphics = () => ({
    frames, raf: activeRaf.size,
    activeObservers: Object.fromEntries(Object.entries(observerSets).map(([key, set]) => [key, set.size])),
    canvases: [...document.querySelectorAll('canvas[role=img]')].map(canvas => ({
      label: canvas.getAttribute('aria-label'), rect: canvas.getBoundingClientRect().toJSON(), ...records.get(canvas),
    })),
    drawCallbackMs: samples.slice(-3000), recentFrames: recentFrames.slice(-180), hidden: document.hidden,
  });
}
