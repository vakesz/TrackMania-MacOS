const repository = "vakesz/TrackMania-MacOS";
const releaseFallback = `https://github.com/${repository}/releases/latest`;

const downloadButtons = document.querySelectorAll("#download-button, #download-button-bottom");
const releaseNote = document.querySelector("#release-note");

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
    if (releaseNote) releaseNote.textContent = `${release.tag_name} for Apple Silicon. Free and open source.`;
  } catch {
    for (const button of downloadButtons) button.href = releaseFallback;
  }
}

loadLatestRelease();
