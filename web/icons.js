// Sabi's icons: simple line drawings in the text colour, so they look the same on every phone (emoji don't)
// and follow light and dark mode. Use <span data-icon="mic"></span> in markup, or SabiIcon("mic") in code.
(function () {
  const svg = body => `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
  const ICONS = {
    camera: svg('<path d="M3.5 8.5a1.5 1.5 0 0 1 1.5-1.5h2.2l1.6-2.2h6.4l1.6 2.2H19a1.5 1.5 0 0 1 1.5 1.5v9.5A1.5 1.5 0 0 1 19 19.5H5A1.5 1.5 0 0 1 3.5 18z"/><circle cx="12" cy="13" r="3.6"/>'),
    mic: svg('<rect x="9" y="3" width="6" height="11.5" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0"/><path d="M12 17.5V21"/>'),
    stop: svg('<rect x="7" y="7" width="10" height="10" rx="2.2" fill="currentColor" stroke="none"/>'),
    // Talk mode: a live sound wave, so it reads as "conversation", not "record a note"
    talk: svg('<path d="M3.5 12h1M7 9v6M10.5 6v12M14 8.5v7M17.5 10v4M20.5 12h0"/>'),
    listening: svg('<circle cx="12" cy="12" r="4" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="8" opacity=".45"/>'),
    thinking: svg('<circle cx="6" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="18" cy="12" r="1.6" fill="currentColor" stroke="none"/>'),
    speaking: svg('<path d="M4 9.5h3.2L11.5 6v12l-4.3-3.5H4z"/><path d="M15 9.2a4 4 0 0 1 0 5.6"/><path d="M17.8 6.6a7.6 7.6 0 0 1 0 10.8"/>'),
    send: svg('<path d="M4.5 12 19.5 5l-4.6 14.5-3.4-6.3z"/><path d="M11.5 13.2 19.5 5"/>'),
  };
  window.SabiIcon = name => ICONS[name] || "";
  const fill = root => (root || document).querySelectorAll("[data-icon]").forEach(el => {
    if (!el.querySelector(":scope > svg.ico")) el.insertAdjacentHTML("afterbegin", ICONS[el.dataset.icon] || "");
  });
  window.SabiIcons = { fill };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => fill());
  else fill();
})();
