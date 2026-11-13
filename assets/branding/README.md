# TrackMania Mac hub icon

`AppIcon.icon` is the editable source for the native hub icon. It is an
Icon Composer document: open it with Icon Composer, which ships inside Xcode 26
and later, or edit `icon.json` and the layers under `Assets/` by hand.

The mark is original: slanted "TM" lettering over three road lanes on midnight
blue. It does not reuse a TrackMania, Nadeo, Ubisoft, or game-executable logo.

The app packager compiles the document with `actool` into the generated `.app`
bundle. That writes `Assets.car`, which macOS 26 and later read for the layered
icon and its dark, tinted and clear variants, and `AppIcon.icns`, the flat icon
earlier systems show. Packaging the hub therefore needs Xcode 26 or later.

`trackmania-mac-icon.png` and the `app-icon-*.png` files under
`website/assets/` are flat exports of the default appearance. Refresh them
after changing the icon:

```sh
ictool="/Applications/Xcode.app/Contents/Applications/Icon Composer.app/Contents/Executables/ictool"
"$ictool" assets/branding/AppIcon.icon --export-image --output-file /tmp/icon.png \
  --platform macOS --rendition Default --width 1024 --height 1024 --scale 1
sips -z 512 512 /tmp/icon.png --out assets/branding/trackmania-mac-icon.png
sips -z 192 192 /tmp/icon.png --out website/assets/app-icon-192.png
sips -z 96 96 /tmp/icon.png --out website/assets/app-icon-96.png
```
