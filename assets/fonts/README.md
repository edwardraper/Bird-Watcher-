# Fonts

Empty on purpose. Fonts are resolved at runtime from a candidate list, and
this directory is searched first — so dropping a `.ttf` in here overrides
the system font without touching any code.

The candidate list lives in `birddisplay/render/fonts.py`. Every style
falls back to something in `fonts-dejavu-core`, which means nothing is ever
missing on a stock Raspberry Pi OS; italic simply degrades to upright if
`fonts-liberation2` is not installed.

```bash
sudo apt install -y fonts-dejavu-core fonts-liberation2
```

Whatever you choose, check it at 11px on the actual panel before committing
to it. Text is drawn aliased (no antialiasing, by design — dithered small
text is unreadable), so fonts with fine hairlines fall apart at the sizes
the footer uses.
