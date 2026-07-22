// Injected into every page via addInitScript: a fake cursor dot that
// moves along one-side-bowed bezier curves with Fitts-scaled timing,
// overshoots long moves, ripples on click, and drives smooth scrolling.
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
    width: '32px',
    height: '32px',
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
  // Asymmetric ease: quick acceleration, long deceleration into the target.
  const ease = (t) => {
    const a = Math.pow(t, 1.6);
    return a / (a + Math.pow(1 - t, 2.4));
  };
  let cx = window.innerWidth / 2;
  let cy = window.innerHeight / 2;
  const put = (x, y) => {
    cx = x;
    cy = y;
    dot.style.left = x + 'px';
    dot.style.top = y + 'px';
  };
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
  // One leg along a cubic bezier bowed to a single side of the direct
  // line (two-sided wobble reads fake; ghost-cursor's rule).
  const leg = async (x1, y1, ms) => {
    const x0 = cx;
    const y0 = cy;
    const dx = x1 - x0;
    const dy = y1 - y0;
    const dist = Math.hypot(dx, dy);
    if (dist < 1) return;
    const side = Math.random() < 0.5 ? 1 : -1;
    const bow = side * Math.min(90, Math.max(8, dist * 0.15));
    const nx = -dy / dist;
    const ny = dx / dist;
    const c1x = x0 + dx * 0.3 + nx * bow;
    const c1y = y0 + dy * 0.3 + ny * bow;
    const c2x = x0 + dx * 0.7 + nx * bow * 0.6;
    const c2y = y0 + dy * 0.7 + ny * bow * 0.6;
    await animate((p) => {
      const q = 1 - p;
      put(
        q * q * q * x0 + 3 * q * q * p * c1x + 3 * q * p * p * c2x + p * p * p * x1,
        q * q * q * y0 + 3 * q * q * p * c1y + 3 * q * p * p * c2y + p * p * p * y1
      );
    }, ms);
  };
  window.__cursor = {
    show() {
      ensure();
      put(cx, cy);
      dot.style.opacity = '1';
    },
    async moveTo(x, y, ms) {
      ensure();
      const dist = Math.hypot(x - cx, y - cy);
      // Fitts-flavored default; an explicit ms stays as a jittered hint.
      const base = ms ?? 260 + 140 * Math.log2(dist / 40 + 1);
      const dur = base * (0.88 + Math.random() * 0.24);
      if (dist > 500) {
        // Ballistic overshoot past the target, then a short correction.
        const ux = (x - cx) / dist;
        const uy = (y - cy) / dist;
        const ov = 8 + Math.random() * 6;
        await leg(x + ux * ov, y + uy * ov, dur * 0.85);
        await leg(x, y, 130);
      } else {
        await leg(x, y, dur);
      }
    },
    async pulse() {
      ensure();
      const ring = document.createElement('div');
      Object.assign(ring.style, {
        position: 'fixed',
        left: cx + 'px',
        top: cy + 'px',
        width: '32px',
        height: '32px',
        borderRadius: '50%',
        border: '3px solid rgba(255,255,255,0.9)',
        zIndex: '2147483646',
        pointerEvents: 'none',
        transform: 'translate(-50%, -50%)',
      });
      document.documentElement.appendChild(ring);
      ring.animate(
        [
          { transform: 'translate(-50%,-50%) scale(0.6)', opacity: 0.9 },
          { transform: 'translate(-50%,-50%) scale(2.6)', opacity: 0 },
        ],
        { duration: 420, easing: 'cubic-bezier(0.2, 0.6, 0.4, 1)' }
      );
      dot.animate(
        [
          { transform: 'translate(-50%,-50%) scale(1)' },
          { transform: 'translate(-50%,-50%) scale(0.82)' },
          { transform: 'translate(-50%,-50%) scale(1)' },
        ],
        { duration: 240, easing: 'ease-out' }
      );
      setTimeout(() => ring.remove(), 450);
      await new Promise((r) => setTimeout(r, 240));
    },
    async scrollTo(y, ms = 900) {
      const y0 = window.scrollY;
      await animate((p) => window.scrollTo(0, y0 + (y - y0) * p), ms);
    },
  };
})();
