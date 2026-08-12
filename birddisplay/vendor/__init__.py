"""Vendored Waveshare e-Paper driver.

Copied verbatim from waveshareteam/e-Paper (MIT licence), path
RaspberryPi_JetsonNano/python/lib/waveshare_epd/. Two files rather than a
dependency on the whole tree, so deployment is reproducible.

Do not edit these -- birddisplay.display.epaper wraps them instead. In
particular we never call epd.getbuffer(): it re-quantises with dithering,
which would ruin our text. See render/palette.pack_for_panel().
"""
