// Injected into every page via addInitScript: a fake cursor dot that
// glides with easing, pulses on click, and drives smooth scrolling.
// Headless recordings don't capture the OS cursor, so scenes animate
// this element instead and Node drives it via window.__cursor.
(() => {
  if (window.__cursor) return;
  const dot = document.createElement('div');
  dot.id = '__promo_cursor';
  Object.assign(dot.style, {
    position: 'fixed',
    left: '0px',
    top: '0px',
    width: '26px',
    height: '26px',
    borderRadius: '50%',
    background: 'rgba(20,20,20,0.85)',
    border: '2.5px solid rgba(255,255,255,0.95)',
    boxShadow: '0 2px 8px rgba(0,0,0,0.35)',
    zIndex: '2147483647',
    pointerEvents: 'none',
    transform: 'translate(-50%, -50%)',
    opacity: '0',
  });
  const ensure = () => {
    if (!dot.isConnected) document.documentElement.appendChild(dot);
  };
  const ease = (t) =>
    t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  let cx = window.innerWidth / 2;
  let cy = window.innerHeight / 2;
  const animate = (fn, ms) =>
    new Promise((resolve) => {
      const t0 = performance.now();
      const step = (now) => {
        const t = Math.min(1, (now - t0) / ms);
        fn(ease(t));
        if (t < 1) requestAnimationFrame(step);
        else resolve();
      };
      requestAnimationFrame(step);
    });
  window.__cursor = {
    show() {
      ensure();
      dot.style.left = cx + 'px';
      dot.style.top = cy + 'px';
      dot.style.opacity = '1';
    },
    async moveTo(x, y, ms = 700) {
      ensure();
      const x0 = cx;
      const y0 = cy;
      await animate((p) => {
        cx = x0 + (x - x0) * p;
        cy = y0 + (y - y0) * p;
        dot.style.left = cx + 'px';
        dot.style.top = cy + 'px';
      }, ms);
    },
    async pulse() {
      ensure();
      dot.animate(
        [
          { transform: 'translate(-50%,-50%) scale(1)' },
          { transform: 'translate(-50%,-50%) scale(0.7)' },
          { transform: 'translate(-50%,-50%) scale(1)' },
        ],
        { duration: 260, easing: 'ease-out' }
      );
      await new Promise((r) => setTimeout(r, 260));
    },
    async scrollTo(y, ms = 900) {
      const y0 = window.scrollY;
      await animate((p) => window.scrollTo(0, y0 + (y - y0) * p), ms);
    },
  };
})();
