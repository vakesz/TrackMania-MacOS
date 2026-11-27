# Download links

Installers and dedicated server files for older TrackMania games with the
Stadium environment, kept here for reference when adding or checking a title.

Adapted from
[ModTMNF's DownloadLinks.md](https://github.com/pixeltris/ModTMNF/blob/master/ModTMNF/Analysis/Docs/DownloadLinks.md)
(MIT, pixeltris). Many of these hosts are old and may go offline; check
archive.org if a link is dead. Hashes were taken from the original files.
Dates are YYYY-MM-DD.

The `source_url` and `source_sha256` of `config/titles/tmnf.json` and
`config/titles/tmu.json` match the TMNF and TMUF entries below, and
`config/titles/tmn-eswc.json` matches the ESWC entry, including its update and
community build. The updates of `config/titles/tm-sunrise-extreme.json` match
the Sunrise eXtreme entry. The registry is the source of truth; this page is
background only.

## Game installers

### TrackMania Nations ESWC (TMN)

- Website: http://eswc.trackmanianations.com
- Installer: http://download.nvidia.com/downloads_emea/Trackmania_Nations/TmNationsESWC_Setup.exe
  - Version 0.1.7.5 (`TmNationsESWC.exe`, 2006-01-26)
  - MD5 `25DD8F408085DBCCED1A4C1757C4080A`
  - SHA-1 `7B004BCE8C4CFD10B1997E9FF514C4388B0B9CF1`
  - SHA-256 `30873AB16E9F81645495002AA4C0416858DB91A67F314E26E6533D5FE906095B`
- Official patches, needed for online play:
  https://community.pcgamingwiki.com/files/file/1348-trackmania-nations-eswc-175-to-179-179-to-180
  - `tmnationseswc_175_to_179_setup.exe`
    - MD5 `E850B3C67A72B4FF8EFB1FAB8A655063`
    - SHA-1 `502AE12133C5030817A7DA875349756C3278E3E4`
    - SHA-256 `8F42E6B0F9058360CE497363CBCBC022FD9D4385A84606DB9B0D65A2ABCDB5F7`
    - Mirror used by the hub:
      https://archive.org/download/tm-nations-eswc-setup/tmnationseswc_175_to_179_setup.exe
      (19194902 bytes)
  - `tmnationseswc_179_to_180_setup.exe`
    - MD5 `8169FFCE52C4D75F0A968DF92B6BB489`
    - SHA-1 `67E27F6E06AE312909CA820A04BB2108FA541DA4`
    - SHA-256 `30F5273D7E6C6D50E61169C369E1D8E2A84D8E5AFA1BF8F9A7E9BD40D43C948F`
  - The hub does not use this update, because the community build below
    replaces everything it ships.
- The game is protected by StarForce DRM (3.07.012.005), which needs kernel
  drivers that Wine cannot run. Every official build (0.1.7.5, 1.7.9, 1.8.0) is
  protected, and the game data is encrypted inside `tmn.dat`. The hub therefore
  installs the community build below over the official one. It is unofficial,
  third-party and of unknown origin, and is trusted only through the pinned
  hashes.
- Community StarForce-free build:
  https://archive.org/download/tm-nations-eswc-setup/Win10Patch.tar.gz
  - Version 1.8.0, 67468269 bytes
  - SHA-256 `B2EC4F34A0FC1009175792F9F2E3130BDF242379235A1AD4C50019117EBB7460`
  - Contains an unpacked `TmNationsESWC.exe`, SHA-256
    `381213323695BA9E3C3E19BEAFD6E063F3A155C1034838B317C40187915B2560`
    (matches ModTMNF's DownloadLinks.md), and about 1,200 decrypted GameData
    and Resource files.
  - The hub unpacks it over the game folder, then removes `tmn.exe`, `tmn.dll`
    and `tmn.dat`.
  - After unpacking, the hub sets the NX_COMPAT flag on `TmNationsESWC.exe`,
    `binkw32.dll` and `unicows.dll` (see "Memory protection flag" in
    `docs/runtime.md`), which gives the executable SHA-256
    `3D5FE22A4EEFF7D0C7B11AAFC0CCBF5610ADAC1DA79260FEEDBF60AB12593764`. It then
    adds the particle patch (see "Nations ESWC particle speed patch" in
    `docs/runtime.md`). The installed executable's SHA-256 is therefore
    `822DEF6627F020035EF0819CF9F85493E952A2EFB450D23770B330311AD8109D`, not the
    archive's `381213...2560`.
  - Like every Windows program in a Wine prefix, the game can read and write
    your files through Wine's Z: drive. The pinned hash is the only check on
    this unofficial executable.

### TrackMania Nations Forever (TMNF)

- Website: https://www.trackmaniaforever.com
- Installer: https://nadeo-download.cdn.ubi.com/trackmaniaforever/tmnationsforever_setup.exe
  - Version 2.11.26 (`TmForever.exe`, 2011-01-28)
  - MD5 `2A36D70989F94BA9369993749FF20640`
  - SHA-1 `23388798D5C90AD4A233B4CD7E9FCAFD69756978`
  - SHA-256 `2F659138ED4409DA404970841E18F03D29921BEAF6A424824C8312DDB20F6355`
  - Changelog: http://www.tm-forum.com/viewtopic.php?t=19288

### TrackMania United Forever (TMUF)

- Player portal (stats, download link): https://player.trackmania.com
- Steam store: https://store.steampowered.com/app/7200/Trackmania_United_Forever
- Installer: http://files.trackmaniaforever.com/tmunitedforever_setup.exe
  - Version 2.11.26 (`TmForever.exe`, 2010-03-15)
  - MD5 `B9D749B4718B480C1F76196DB90970BD`
  - SHA-1 `C5E45181D9BFD96254616EC3C8C896DBDE7D3B23`
  - SHA-256 `F8A4D09C64D65BE271C0346F5B0E41529CB25EB8C605D4602D0F34F27FB1355F`
  - Changelog: http://www.tm-forum.com/viewtopic.php?t=19288
- Manual: https://cdn.akamai.steamstatic.com/steam/apps/7200/manuals/manual.pdf
  (same content as `TMUnited-Game-Manual.pdf` on the TMU DVD, different hash)
- Notes:
  - Steam is the only official place to buy the game.
  - TMU box keys don't work on the Steam version.
  - "Upgrade to United" in TMNF opens http://smarturl.it/TMUF?affilie=N2U,
    which leads to a store (gamesload.com) that no longer sells TMUF.

### TrackMania Sunrise eXtreme (TMSX)

Not a Stadium game, listed here because the hub pins its updates. The hub
installs Sunrise from your own retail disc (`TmSunriseSetup.exe`, installs to
`TrackMania Sunrise`) and pins no installer for it.

- eXtreme was a free expansion update for Sunrise. The official updates are no
  longer on a Nadeo host. The hub downloads them from a community mirror on
  archive.org (https://archive.org/details/tm-sunrise-extreme-update), which is
  unofficial and trusted only through the pinned hashes:
  - `TmSunriseExtreme_update.exe`, "TrackMania Sunrise Extreme 1.5.0" (Inno
    Setup, setup data 5.1.2):
    https://archive.org/download/tm-sunrise-extreme-update/TmSunriseExtreme_update.exe
    - 417826835 bytes
    - SHA-1 `F3D59B269A6AEF167BA095548AD424C3425E5EEF`
    - SHA-256 `179E8D5D2437FF25D92A5FE840BD798DF7B5522F7189A4A48E1B7FDA8E097844`
    - Its setup script reads the Sunrise install from the `TmSunrise_is1`
      uninstall key and expects Sunrise version 0.1.4.6. A retail disc with an
      older build may first need an official Sunrise patch; this has not been
      checked.
  - `TmSunriseExtreme_150_to_151.exe`, "TrackMania Sunrise 1.5.1" (Inno Setup,
    setup data 5.1.10):
    https://archive.org/download/tm-sunrise-extreme-update/TmSunriseExtreme_150_to_151.exe
    - 21567628 bytes
    - SHA-1 `605DB655F6F7AF025DD8709C155CC80205660677`
    - SHA-256 `9E456A76C39F9E528E831512A94C640E58C266190A5BDB6820E2E2A0D70A0F21`
  - Both ship `tms.exe`, `tms.dll` and `tms.dat` or `tms.msg`: the game is
    protected by StarForce, which Wine cannot run. Whether both run with the
    silent Inno Setup switches has not been checked with a real install.
- The same archive.org item has `TrackmaniaSunriseExtreme_Win10Patch.zip`
  (135814066 bytes, SHA-256
  `DE3EC4CCE962BA2D1283BB37FB7458C53BE684355FADAF79BCAB597D3411F883`). The hub
  does not use it. It is an unofficial build of unknown origin: a
  StarForce-free `TmSunrise.exe` (SHA-256
  `E59DFC7EAA5314E85B2B0DF6F40EF7BA01A23BD5A9525F84407F5DFF301DF4FE`), about
  4,240 decrypted GameData and Resource files, and a third-party proxy
  `d3d9.dll` (SHA-256
  `B5674754A1F5D7A4A900FEA0522A7E7C99ACA6B2CB1A8BDC10F48369CB776566`) that
  would sit in front of D9VK. Its readme says to copy it over the game folder
  after the updates. The item also has a binary `Default.SystemConfig.Gbx`
  settings preset, which the hub does not use either.

### Other

- Official portal for the TrackMania games: https://www.trackmania.com/explore
- Maniaplanet (TM2): https://www.maniaplanet.com/download
- Trackmania (2020): https://www.ubisoft.com/en-us/game/trackmania/trackmania

http://www.tm-forum.com has many more links, patches and older builds.

## Dedicated servers

### TrackMania Nations ESWC (TMN)

- All official server links: http://www.tm-forum.com/viewtopic.php?f=28&t=1350
- http://slig.free.fr/TM/dedicated/TmDedicatedServer_2006-05-30.zip
  - Newest release (2006-05-30), mirrored by Slig from tm-forum
  - MD5 `3F77A09CABAEBB0B9A97F0D15E6DA1BD`
  - SHA-1 `8DB671024877508925402EEC396BDBDA611AD6C7`
  - SHA-256 `28D79550FDD18623C47D3A428AF5CE8E150402B8C4C5FA0B45D3EFA4E6BF783D`

### TrackMania United Forever and Nations Forever (TMUF, TMNF)

- All official server links, changelogs and references:
  http://www.tm-forum.com/viewtopic.php?t=14203
- http://files2.trackmaniaforever.com/TrackmaniaServer_2011-02-21.zip
  - Newest release (2011-02-21)
  - MD5 `7FA8F922E0165EE7E646203523212FCA`
  - SHA-1 `03D80B9418937BD250B8E69D93604EEE07F59459`
  - SHA-256 `DD021F49C3D58D45EE09F333015BDB35B19243A38FA65F202CA8A88FB6550C0B`

### Maniaplanet (TM2)

- Setup guide: https://forum.maniaplanet.com/viewtopic.php?f=261&t=43622
- Always current: http://files.v04.maniaplanet.com/server/ManiaplanetServer_Latest.zip
- Server links and changelogs:
  https://forum.maniaplanet.com/viewtopic.php?f=261&t=46287 and
  https://forum.maniaplanet.com/viewtopic.php?f=261&t=12494
- http://files.v04.maniaplanet.com/server/ManiaplanetServer_2019-10-23.zip
  - Newest dated release (2019-10-23)
  - MD5 `02C95C7A51B9D9B32CD1320FD1BEFDD8`
  - SHA-1 `7B7690EE854A2DDFBBEC54C512AA414F64BCA6E1`
  - SHA-256 `7EF745E0EE8B97E6FC3514D0D8244E2410DF321E748F790BADD0F5CC95E02E5F`
- http://files.v04.maniaplanet.com/setups/ManiaPlanetLogs.zip
  - "Logs" build of the game, with assertions that include code snippets,
    line numbers and source file paths (2019-11-21)
  - MD5 `10374C7C583DFC2C2D6E79CAC0C0D74B`
  - SHA-1 `3A1B5217284F72051A2B21440DD2B92E265ACA0C`
  - SHA-256 `F18E47597FA1BF8CCF8CFB743071460EE9FB348CD47141E83E3D012B2DCBFA56`

### Trackmania (2020)

- Setup guide:
  https://forums.ubisoft.com/showthread.php/2242192-Tutorial-Trackmania-2020-Dedicated-Server
- Always current: http://files.v04.maniaplanet.com/server/TrackmaniaServer_Latest.zip
- Server links and changelogs:
  https://forums.ubisoft.com/showthread.php/2242492-Dedicated-Server-binaries
