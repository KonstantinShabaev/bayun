// Screen helpers for voice control: numbered list of visible clickable elements
// and YouTube search results. Injected over CDP next to overlay.js.
(() => {
  const VERSION = 4;
  if (window.__bayunScreen && window.__bayunScreen.version >= VERSION) return;

  const CLICKABLE = 'a[href], button, [role=button], [role=link], [role=tab], [role=menuitem], ' +
    'input:not([type=hidden]), select, textarea, [tabindex]:not([tabindex="-1"]), ytd-thumbnail, ' +
    'ytd-rich-item-renderer, ytd-video-renderer, ytd-compact-video-renderer';

  const clean = s => (s || '').replace(/\s+/g, ' ').trim();

  function label(el) {
    return clean(el.getAttribute('aria-label') || el.getAttribute('title') ||
      (el.matches('input, textarea') ? el.placeholder || el.value : '') ||
      el.innerText || el.textContent).slice(0, 90);
  }

  function visibleRect(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return null;
    if (r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) return null;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || +cs.opacity === 0) return null;
    // the element must actually be on top at its centre (not covered by a dialog)
    const cx = Math.min(innerWidth - 1, Math.max(0, r.left + r.width / 2));
    const cy = Math.min(innerHeight - 1, Math.max(0, r.top + r.height / 2));
    const top = document.elementFromPoint(cx, cy);
    if (top && top !== el && !el.contains(top) && !top.contains(el)) return null;
    return r;
  }

  let items = [];

  function list(max = 40) {
    const seen = new Set();
    const out = [];
    for (const el of document.querySelectorAll(CLICKABLE)) {
      if (out.length >= max) break;
      // prefer the outermost clickable for a given label (a card, not its inner link)
      if ([...seen].some(s => s.contains(el))) continue;
      const r = visibleRect(el);
      if (!r) continue;
      const text = label(el);
      if (!text) continue;
      seen.add(el);
      out.push({el, text, r});
    }
    out.sort((a, b) => (a.r.top - b.r.top) || (a.r.left - b.r.left));
    items = out.map((it, i) => ({...it, n: i + 1}));
    return items.map(({n, text, el, r}) => ({
      n, text, tag: el.tagName.toLowerCase(),
      x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height),
    }));
  }

  function point(n) {
    const it = items.find(i => i.n === n);
    if (!it || !it.el.isConnected) return null;
    it.el.scrollIntoView({block: 'nearest', inline: 'nearest'});
    const r = it.el.getBoundingClientRect();
    return {x: r.left + r.width / 2, y: r.top + r.height / 2, text: it.text};
  }

  function findText(text) {
    const want = clean(text).toLowerCase();
    if (!want) return null;
    const all = list(200);
    const score = t => {
      const s = t.toLowerCase();
      if (s === want) return 3;
      if (s.startsWith(want)) return 2;
      return s.includes(want) ? 1 : 0;
    };
    const best = all.map(i => [score(i.text), i]).filter(([s]) => s > 0)
      .sort((a, b) => b[0] - a[0] || a[1].text.length - b[1].text.length)[0];
    return best ? best[1].n : null;
  }

  function youtubeResults(max = 10) {
    const out = [];
    for (const r of document.querySelectorAll('ytd-video-renderer, ytd-rich-item-renderer')) {
      if (out.length >= max) break;
      if (r.querySelector('ytd-ad-slot-renderer, [is-ad], ytd-display-ad-renderer')) continue;
      const a = r.querySelector('a#video-title, a#video-title-link, a.yt-lockup-metadata-view-model-wiz__title, h3 a');
      const href = a && a.href;
      if (!href || !/\/watch\?v=/.test(href)) continue;  // skip shorts, channels, playlists
      const box = (r.querySelector('ytd-thumbnail, yt-thumbnail-view-model') || r).getBoundingClientRect();
      const id = (href.match(/[?&]v=([\w-]{11})/) || [])[1];
      out.push({
        thumb: id ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : '',
        n: out.length + 1,
        x: Math.round(box.left), y: Math.round(box.top), w: Math.round(box.width), h: Math.round(box.height),
        title: clean(a.getAttribute('title') || a.textContent),
        channel: clean((r.querySelector('ytd-channel-name a, #channel-name a, ytd-channel-name') || {}).textContent),
        duration: clean((r.querySelector('ytd-thumbnail-overlay-time-status-renderer, badge-shape .badge-shape-wiz__text, .yt-badge-shape__text') || {}).textContent),
        url: href.replace(/&.*$/, ''),
      });
    }
    return out;
  }

  // The main <video>, also inside shadow roots (VK puts its player in one): the longest one with a source.
  function videos() {
    const found = [];
    const walk = root => {
      for (const el of root.querySelectorAll('*')) {
        if (el.tagName === 'VIDEO') found.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(document);
    return found;
  }

  function video() {
    const found = [];
    const walk = root => {
      for (const el of root.querySelectorAll('*')) {
        if (el.tagName === 'VIDEO') found.push(el);
        if (el.shadowRoot) walk(el.shadowRoot);
      }
    };
    walk(document);
    const withSrc = found.filter(v => v.currentSrc || v.src);
    const pool = withSrc.length ? withSrc : found;
    return pool.sort((a, b) => (isFinite(b.duration) ? b.duration : 0) - (isFinite(a.duration) ? a.duration : 0))[0] || null;
  }

  // Click the first visible control whose label matches, searching shadow roots too.
  function clickDeep(re) {
    const walk = root => {
      for (const el of root.querySelectorAll('button, [role=button], a, div[class*="skip" i]')) {
        const t = (el.getAttribute('aria-label') || el.innerText || el.textContent || '').trim();
        if (re.test(t) && el.getClientRects().length) { el.click(); return t; }
      }
      for (const el of root.querySelectorAll('*')) if (el.shadowRoot) { const r = walk(el.shadowRoot); if (r) return r; }
      return null;
    };
    return walk(document);
  }

  function vkResults(max = 10) {
    const out = [];
    for (const card of document.querySelectorAll('[data-testid="video_card_layout"]')) {
      if (out.length >= max) break;
      const a = card.querySelector('a[href*="/video-"], a[href*="/video"]');
      if (!a) continue;
      const pick = id => clean((card.querySelector(`[data-testid="${id}"]`) || {}).textContent);
      const thumb = (card.querySelector('[data-testid="video_card_thumb"]') || card).getBoundingClientRect();
      out.push({
        n: out.length + 1, title: pick('video_card_title'), channel: pick('video_card_owner'),
        thumb: ((card.querySelector('[data-testid="video_card_thumb"] img, img') || {}).currentSrc) || '',
        duration: pick('video_card_duration'), url: a.href.replace(/[?#].*$/, ''),
        x: Math.round(thumb.left), y: Math.round(thumb.top), w: Math.round(thumb.width), h: Math.round(thumb.height),
      });
    }
    return out.filter(r => r.title);
  }

  window.__bayunScreen = {version: VERSION, list, point, findText, youtubeResults, vkResults, video, videos, clickDeep};
})();
