const repository = "vakesz/TrackMania-MacOS";
const releaseFallback = `https://github.com/${repository}/releases/latest`;

const downloadButtons = document.querySelectorAll("#download-button, #download-button-bottom");

// Point the download buttons straight at the hub zip of the newest release.
// Without network or on an API error, they keep linking to the releases page.
async function loadLatestRelease() {
  try {
    const response = await fetch(`https://api.github.com/repos/${repository}/releases/latest`, {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (!response.ok) throw new Error(`GitHub returned ${response.status}`);

    const release = await response.json();
    const download = release.assets?.find((asset) => asset.name.endsWith(".app.zip"));
    if (!download) return;

    for (const button of downloadButtons) {
      button.href = download.browser_download_url;
      button.querySelector("span").textContent = `Download ${release.tag_name}`;
    }
  } catch {
    for (const button of downloadButtons) button.href = releaseFallback;
  }
}

loadLatestRelease();

// A lap of the page: the start lights count down, the chrono runs, and
// reaching the finish section stops it and awards a medal. Purely for fun;
// nothing is stored or sent anywhere.
const gantry = document.querySelector("#gantry");
const chrono = document.querySelector("#chrono");
const finishLine = document.querySelector("#finish");
const finishTime = document.querySelector("#finish-time");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const medals = [
  { name: "author", label: "Author medal", under: 15000 },
  { name: "gold", label: "Gold medal", under: 30000 },
  { name: "silver", label: "Silver medal", under: 60000 },
  { name: "bronze", label: "Bronze medal", under: Infinity },
];

let startedAt = null;
let frame = 0;
let timers = [];

function formatTime(ms) {
  const hundredths = Math.floor(ms / 10);
  const minutes = Math.floor(hundredths / 6000);
  const seconds = Math.floor(hundredths / 100) % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(hundredths % 100).padStart(2, "0")}`;
}

function tick(now) {
  chrono.textContent = formatTime(now - startedAt);
  frame = requestAnimationFrame(tick);
}

function go() {
  gantry.dataset.state = "go";
  startedAt = performance.now();
  delete chrono.dataset.finished;
  finishTime.hidden = true;
  if (!reducedMotion) frame = requestAnimationFrame(tick);
}

function startRace() {
  cancelAnimationFrame(frame);
  timers.forEach(clearTimeout);
  startedAt = null;
  chrono.textContent = formatTime(0);
  if (reducedMotion) {
    go();
    return;
  }
  gantry.dataset.state = "";
  timers = [1, 2, 3].map((light) => setTimeout(() => (gantry.dataset.state = String(light)), light * 600));
  timers.push(setTimeout(go, 4 * 600));
}

function finish() {
  if (startedAt === null || chrono.dataset.finished !== undefined) return;
  cancelAnimationFrame(frame);
  const elapsed = performance.now() - startedAt;
  const medal = medals.find((m) => elapsed < m.under);
  chrono.textContent = formatTime(elapsed);
  chrono.dataset.finished = "";

  const disc = document.createElement("span");
  disc.className = "medal";
  disc.dataset.medal = medal.name;
  disc.setAttribute("aria-hidden", "true");
  finishTime.replaceChildren(disc, `Finish in ${formatTime(elapsed)}. ${medal.label}.`);
  finishTime.hidden = false;
}

if (gantry && chrono && finishLine && finishTime) {
  new IntersectionObserver((entries) => {
    if (entries.some((entry) => entry.isIntersecting)) finish();
  }, { threshold: 0.4 }).observe(finishLine);

  chrono.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: reducedMotion ? "auto" : "smooth" });
    startRace();
  });

  startRace();
}
