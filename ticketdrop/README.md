# Ticket-drop notifier

A personal, non-commercial watcher for one FIXR venue (Timepiece Exeter, venue
2783). It runs on a Raspberry Pi Pico 2 W, the one inside a Pimoroni Inky Frame
7.3". Once a minute it checks the venue's event list. When an event appears
that it hasn't seen before, it sends you a WhatsApp message through CallMeBot
and a Telegram message.

This project has no connection to FIXR, Timepiece, or any existing ticket-alert
service. It only reads the public event list. It never books or checks out.

> **Status:** probe stage. Only `probe.py`, `config.py` and
> `secrets.example.py` exist so far. The notifier comes next, once the probe has
> confirmed the real response format.

## 1. Flash Pimoroni's Inky Frame firmware

1. Download the latest Inky Frame release from
   <https://github.com/pimoroni/inky-frame/releases>. Pick the `.uf2` built for
   the **Pico 2 W / RP2350** (not the original Pico W build).
2. Unplug the frame. Hold the **BOOTSEL** button on the Pico board on the back,
   plug in USB, then let go. A drive called `RP2350` appears.
3. Drag the `.uf2` onto that drive. The board reboots into MicroPython.

The firmware comes with Pimoroni's demo `main.py`, and sometimes a
`secrets.py`. The notifier's `main.py` and `secrets.py` will overwrite them
later. To keep the demos, back them up first with
`mpremote cp :main.py main.demo.py`.

## 2. Install mpremote on your laptop

```sh
pip install mpremote
mpremote ls          # should list the frame's files
```

## 3. Create a Telegram bot and find your chat id

1. In Telegram, message **@BotFather** and send `/newbot`. Choose a name and a
   username ending in `bot`. BotFather replies with a token like
   `123456789:AA...`. That is `TELEGRAM_BOT_TOKEN`.
2. Open a chat with your new bot and send it any message, for example `hi`.
   Bots can't message you until you message them first.
3. In a browser, open
   `https://api.telegram.org/bot<TOKEN>/getUpdates`, replacing `<TOKEN>` with
   your token. Find `"chat":{"id":123456789,...}`. That number is
   `TELEGRAM_CHAT_ID`. If `result` is empty, send the bot another message and
   reload the page.

## 4. Set up CallMeBot WhatsApp

Follow the current instructions at <https://www.callmebot.com/blog/free-api-whatsapp-messages/>.
You add their number to your contacts and send it the activation phrase from
WhatsApp. It replies with your API key. The number and phrase have changed over
time, so use what their page says now.

- `CALLMEBOT_PHONE` is your own number in international format, e.g. `+447700900000`.
- `CALLMEBOT_APIKEY` is the key they send you.

## 5. Fill in secrets.py

```sh
cd ticketdrop
cp secrets.example.py secrets.py
# edit secrets.py: Wi-Fi, Telegram, CallMeBot
```

`secrets.py` is git-ignored. Don't commit it.

## 6. Run the probe first

The FIXR endpoint isn't documented, so check what it really returns before the
notifier depends on it:

```sh
cd ticketdrop
mpremote cp config.py secrets.py : + run probe.py
```

The probe connects to Wi-Fi and makes one request. It prints:

- the status code
- the response size
- free memory before and after `json.loads`
- the top-level keys
- the number of events
- every field of the first event, plus each event's id and name

It also saves the raw response to `probe.json` on the frame. To copy it back:

```sh
mpremote cp :probe.json .
```

How to read the result:

- **`status: 200` and events listed.** The simple approach works. Paste the
  output back so the notifier can use the real field names, such as the event
  date.
- **`!!! MemoryError in json.loads !!!`.** The full JSON parse doesn't fit in
  RAM, so the notifier needs a chunked reader instead.
- **`403`, `503`, or an HTML body.** The API is probably behind bot protection
  that treats the Pico differently from a browser. Paste the first 400 bytes it
  prints.

## 7. Run on boot (after the notifier is written)

```sh
cd ticketdrop
mpremote cp *.py :
mpremote reset
```

MicroPython runs `main.py` on every boot. Keep the frame on USB power: polling
once a minute with Wi-Fi on would drain the battery pack in about a day.
To watch the log, including free memory on each cycle, run `mpremote repl`
(Ctrl-] to exit).

## Politeness

- One request per minute.
- A normal browser User-Agent.
- Backs off on 429 (honouring `Retry-After`) and on 5xx errors.
- Read-only. It never books tickets or starts a checkout.
