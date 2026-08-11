# Music — `/play`

Play YouTube audio in a voice channel. Join a voice channel first, then use
`/play` with either a **YouTube URL** or a **search phrase**.

## Play — `/play`

- `/play https://youtu.be/...` — play that video's audio
- `/play tubthumping` — search YouTube and play the top result
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
- Search uses YouTube's top match — if it's wrong, paste the exact URL instead.
- `/voice` is different: that one answers a prompt as an MP3 file in chat, not
  voice-channel music.
