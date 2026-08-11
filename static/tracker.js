(() => {
  const script = document.currentScript;
  const key = script?.dataset.site;
  if (!key) return;
  const storageKey = 'fastsocial_visitor';
  let visitor = localStorage.getItem(storageKey);
  if (!visitor) {
    visitor = crypto.randomUUID();
    localStorage.setItem(storageKey, visitor);
  }
  const source = new URL(script.src);
  const pixel = new Image();
  pixel.referrerPolicy = 'no-referrer';
  pixel.src = `${source.origin}/track/${encodeURIComponent(key)}.gif?` + new URLSearchParams({
    p: location.pathname + location.search,
    r: document.referrer,
    v: visitor,
  });
})();
