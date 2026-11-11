// Kiosk-wide page tweaks, injected over CDP on every new document (before the page's own scripts).
// 1. No mouse pointer anywhere: nobody uses a mouse on the TV, and on Wayland there is no unclutter.
// 2. YouTube: the preferred quality goes to localStorage before the player reads it.
(() => {
  if (window.__bayunKiosk) return;
  window.__bayunKiosk = true;

  const css = 'html, body, body *, ::backdrop { cursor: none !important; }';
  const put = () => {
    const parent = document.head || document.documentElement;
    if (!parent) return false;
    let s = document.getElementById('bayun-no-cursor');
    if (!s) {
      s = document.createElement('style');
      s.id = 'bayun-no-cursor';
      s.textContent = css;  // textContent, not innerHTML: fine under YouTube's Trusted Types
    }
    if (s.parentNode !== parent) parent.appendChild(s);
    return true;
  };
  if (!put()) document.addEventListener('readystatechange', put, {once: true});
  document.addEventListener('DOMContentLoaded', put);
  document.addEventListener('fullscreenchange', put);

  if (/(^|\.)youtube\.com$/.test(location.hostname)) {
    try {
      // 0 = auto; otherwise the vertical resolution YouTube stores (2160, 1440, 1080, 720, ...).
      const want = +(localStorage.getItem('bayun-quality') || 1080);
      if (want > 0) {
        const now = Date.now();
        localStorage.setItem('yt-player-quality', JSON.stringify({
          data: JSON.stringify({quality: want, previousQuality: want}),
          expiration: now + 365 * 864e5, creation: now,
        }));
      }
    } catch (e) {}
  }
})();
