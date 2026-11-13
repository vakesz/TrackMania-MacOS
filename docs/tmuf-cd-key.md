# Get your TrackMania United Forever CD key

TrackMania United Forever needs a CD key. The hub keeps **Play** turned off
until the game folder has one. Nations Forever is free and needs no key.

If you bought the game on Steam, the key belongs to your Steam account. Use one
of the ways below to see it.

## Option 1: Steam on a Mac

1. Open Steam and go to **Library**.
2. Right-click **TrackMania United Forever**.
3. Choose **Manage**, then **CD Keys**.
4. Copy the key.

Steam only shows **CD Keys** after it has issued the key. If you don't see it,
use option 2 or 3.

## Option 2: Windows Steam under Wine

Use this if you already run Windows Steam in CrossOver, Whisky or Wine.

1. Install TrackMania United Forever in that Windows Steam.
2. Start the game once through Steam. This is the step that writes the key.
3. Read the key in one of two places:
   - the registry, with
     `wine reg query "HKCU\Software\Valve\TestApp7200" /v SteamKey`
   - the `InstallKey=` line in the game's `Nadeo.ini`.

Downloading the game with SteamCMD does not give you a key, because SteamCMD
never runs the step that writes it.

## Option 3: Ask Steam Support

1. Go to [help.steampowered.com](https://help.steampowered.com).
2. Pick TrackMania United Forever and open a ticket.
3. Ask for the game's legacy CD key.

## Enter the key

1. In the hub, select TrackMania United Forever.
2. Click **Register player key** and enter the key in the game's launcher.
3. Back in the hub, choose **View › Refresh** (⌘R). **Play** turns on once the
   key is found.

If **Play** stays off, open the game folder (**⋯ › Show in Finder**), open
`Nadeo.ini` in TextEdit, and set the line under `[TmForever]` to:

```ini
InstallKey=YOUR-CD-KEY
```

The hub only checks that a key is there: the line above, or the key file the
launcher saves when you register (`pkey.dat` in the game's `Config` folder). It
never reads the key out, stores it or sends it anywhere.
