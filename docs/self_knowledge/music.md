# Music — `/play`

Play SoundCloud audio in a voice channel. Join a voice channel first, then use
`/play` with either a **SoundCloud URL** or a **search phrase**.

Audio is fetched by **Lavalink** (built-in SoundCloud source), not by the Discord
bot process itself. YouTube is not supported (VPS IPs get blocked).

## Play — `/play`

- `/play https://soundcloud.com/...` — play that track
- `/play lofi hip hop` — search SoundCloud and play the top result
- If something is already playing, new requests are **queued**

Also works with the `_play` prefix.

## Controls

| Command | What it does |
|---------|----------------|
| `/skip` | Skip the current track |
| `/stop` | Stop and clear the queue (bot stays in voice) |
| `/queue` | Show now playing + upcoming tracks |
| `/np` | Show the track playing now |
| `/pause` | Pause |
| `/resume` | Resume |
| `/leave` | Disconnect and clear the queue |

## Tips for members

- You must be in a voice channel before `/play`.
- Search uses SoundCloud's top matches — full streams are tried first; if one
  fails, the bot automatically tries another result (including short previews).
- If a track is preview-only (~30 seconds), the bot will say so. Try another
  upload of the same song for a full play.
- `/voice` is different: that one answers a prompt as an MP3 file in chat, not
  voice-channel music.
