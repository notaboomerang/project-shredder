// Shredder ESPN Connect — popup logic.
// Uses the privileged chrome.cookies API (the ONLY way to read ESPN's
// HttpOnly espn_s2 + SWID cookies; page JS via document.cookie cannot see them)
// and opens Shredder with the cookies as URL params, which the app ingests via
// its existing ?espn_s2=...&swid=... one-tap path.

const SHREDDER_URL = "https://shreddies.streamlit.app";

const btn = document.getElementById("go");
const statusEl = document.getElementById("status");

function setStatus(msg, cls) {
  statusEl.textContent = msg;
  statusEl.className = cls || "";
}

// Read one cookie by name from any espn.com domain. chrome.cookies.getAll
// searches across subdomains, so espn_s2/SWID set on .espn.com or
// fantasy.espn.com are both found.
function getEspnCookie(name) {
  return new Promise((resolve) => {
    chrome.cookies.getAll({ domain: "espn.com", name }, (cookies) => {
      if (cookies && cookies.length) {
        // Prefer the longest value (the real session cookie, not a stub).
        cookies.sort((a, b) => (b.value || "").length - (a.value || "").length);
        resolve(cookies[0].value || "");
      } else {
        resolve("");
      }
    });
  });
}

btn.addEventListener("click", async () => {
  btn.disabled = true;
  setStatus("Reading your ESPN cookies…", "hint");
  try {
    const s2 = await getEspnCookie("espn_s2");
    // SWID cookie name is upper-case; try both spellings to be safe.
    let swid = await getEspnCookie("SWID");
    if (!swid) swid = await getEspnCookie("swid");

    if (!s2 || !swid) {
      setStatus("Couldn't find your ESPN login. Open fantasy.espn.com, log in, " +
                "then click this again.", "err");
      btn.disabled = false;
      return;
    }

    const url = SHREDDER_URL + "/?espn_s2=" + encodeURIComponent(s2) +
                "&swid=" + encodeURIComponent(swid);
    chrome.tabs.create({ url });
    setStatus("✓ Opening Shredder — you'll land connected.", "ok");
    setTimeout(() => window.close(), 900);
  } catch (e) {
    setStatus("Error: " + (e && e.message ? e.message : e), "err");
    btn.disabled = false;
  }
});
