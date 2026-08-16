# The frame

Three files go on the Inky Frame 7.3" (Pico 2 W). Everything else in this
repository runs on a laptop or on a GitHub Actions runner.

```
main.py             wake, ask, maybe draw, sleep
secrets.py          your wifi and the two URLs (from secrets.example.py)
```

## Setting it up

1. **Flash Pimoroni's MicroPython.** Download the latest Inky Frame `.uf2`
   from [pimoroni/inky-frame releases](https://github.com/pimoroni/inky-frame/releases),
   hold BOOTSEL while plugging the frame in, and drag the file onto the
   drive that appears. The build matters: it carries `picographics`,
   `pngdec` and `inky_frame`, none of which are in stock MicroPython.

2. **Copy the files.** Open [Thonny](https://thonny.org), select the board
   as the interpreter, and save `main.py` and your filled-in `secrets.py`
   to the device root.

3. **Bring it up against a laptop first, if you like.** Waiting two hours
   for a workflow to find out whether the panel draws what you drew is a
   miserable loop:

   ```bash
   python -m birddisplay.show --backend inky --preview-path board.png
   python -m scripts.serve_board      # prints the URLs to paste in secrets.py
   ```

   Plain HTTP, no GitHub in the way. Put the raw.githubusercontent.com URLs
   back when it works.

4. **First run on USB**, with Thonny's console open, so you can watch a
   cycle happen and see the log. It will connect, set its clock from NTP,
   download the board and refresh — about a minute in total, most of it the
   panel.

5. **Then move it to battery.** This is not optional if you want the
   two-hour schedule: `inky_frame.sleep_for()` wakes the board by cutting
   its power and letting the RTC restore it, and the RTC cannot cut power
   that is coming down the USB cable. On USB the firmware notices it is
   still running and waits out the interval instead, which is fine for a
   bench and wrong for a wall.

## Check the inks before you trust anything

Pimoroni's docs describe the older seven-colour Inky Frame, not the Spectra 6
board, and the one thing that would ruin every board quietly is a pen order
that does not match. So draw the palette card first:

```bash
python -m scripts.palette_card --backend inky --out board.png
python -m scripts.serve_board
```

Six labelled swatches. If the one labelled GREEN comes out green, the mapping
is right and everything else follows. If green and yellow are swapped,
`INKY_PEN_CODES` in `birddisplay/render/palette.py` is the single line to fix.

## What it does, and what it costs

Every two hours it wakes and downloads `board.json`, a few hundred bytes.
If the headline bird there matches what it drew last time, it goes straight
back to sleep without touching the panel: a quiet cycle is a few seconds of
radio and nothing else. Only when the bird changes does it pull the PNG
(tens of kilobytes) and spend the thirty seconds a Spectra 6 refresh takes.
(The board is republished with a fresh date every two hours, so its sha
changes even when the bird does not — which is why the bird decides.)

Each network request is tried a few times within the wake before the cycle
is given up: the radio is already up by then, so a retry is nearly free,
and it is what gets a new bird onto the glass despite a dropped socket.

Once a day it refreshes anyway, whether or not anything changed. E-ink that
holds the same image for a very long time starts to ghost, and one refresh
a day is the usual advice for preventing it.

Between cycles the board is genuinely off — about 20µA — so battery life is
measured in months, and is set mostly by how often the birds change rather
than by the two-hour tick.

## When something goes wrong

Nothing is drawn. That is the entire error-handling strategy, and it is the
right one for a picture on a wall: an e-ink panel with no power keeps its
last image indefinitely, so a failure leaves a board that is a few hours
out of date instead of a screen that says `OSError: [Errno 110] ETIMEDOUT`.

Failures retry after 15 minutes, three times, then wait for the next
scheduled wake. Plug it into USB and open Thonny to see why.

**Button A forces a refresh** — press it and the frame wakes, downloads and
redraws even if the bird is unchanged. Useful when you have just changed the layout
and do not want to wait two hours to see it.

## Where the board comes from

`.github/workflows/board.yml`, every two hours: it runs the same fetcher and
renderer you can run locally, and force-pushes `board.png` and `board.json`
to the `board` branch. The frame downloads them from `raw.githubusercontent.com`.

The image is a palettised PNG whose indices are already PicoGraphics pen
numbers, and it is decoded with `PNG_COPY`, which copies those indices into
the framebuffer without dithering. That is the one detail worth
remembering: the board's small type was drawn a pixel at a time to be
readable at 11px, and any decode mode that re-dithers — or shipping a JPEG,
as most Inky examples do — undoes it.

To see what the frame will see, without a frame:

```bash
python -m birddisplay.show --backend inky --preview-path board.png
```
