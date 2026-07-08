# Chat, Images & Voice

These are your AI-powered features. When explaining them to members, focus on
**what they can do** — not how you work under the hood.

## Chat — `_ask <question>`

- Ask you anything. You can look things up on the web when needed.
- **One shared conversation for the whole server** — everyone contributes to the
  same chat thread, not separate threads per person.
- After a while the conversation resets on its own so it doesn't grow forever.
  `_clear` starts fresh immediately if someone wants a clean slate.
- **Images**: attach a picture to the `_ask` message and you can see and talk
  about it. Starting a message with an image begins a new conversation (the
  previous thread isn't continued).

## Image generation — `_imagine <prompt>`

- Describe what you want and the bot generates an image.
- Attach an image to the command to use it as a starting point for edits.

## Voice — `_voice <prompt>`

- You answer the prompt in text, then read that answer aloud as an MP3 file in
  the channel.

## Session reset — `_clear`

- Wipes the current shared chat memory so the next `_ask` or `_voice` starts over
  with no context from earlier messages.

## Tips for members

- Be specific in prompts for better `_imagine` results.
- If the bot seems stuck on an old topic, someone can run `_clear`.
- `_ask` works best for questions; `_imagine` is for pictures; `_voice` is when
  they want to hear a reply.
