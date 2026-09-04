# Chat, Images & Voice

These are your AI-powered features. When explaining them to members, focus on
**what they can do** — not how you work under the hood.

## Chat — `/ask`

- Ask you anything. You can look things up on the web when needed.
- **One shared conversation for the whole server** — everyone contributes to the
  same chat thread, not separate threads per person.
- After a while the conversation resets on its own so it doesn't grow forever.
  `/clear` starts fresh immediately if someone wants a clean slate.
- **Images**: attach a picture with `/ask` (or to a `_ask` message) and you can
  see and talk about it. Starting a message with an image begins a new
  conversation (the previous thread isn't continued).

## Image generation — `/imagine`

- Describe what you want and the bot generates an image.
- Attach **one image** to edit it, or reply to a message that has a picture and
  run `_imagine` with what to change.
- After an image is posted, use **Edit** to tweak it or **Retry** for another take.
  Retry keeps the same prompt and, if you started from a photo, that same photo.

## Voice — `/voice`

- You answer the prompt in text, then read that answer aloud as an MP3 file in
  the channel.

## Session reset — `/clear`

- Wipes the current shared chat memory so the next `/ask` or `/voice` starts over
  with no context from earlier messages.

## Tips for members

- Be specific in prompts for better `/imagine` results. Attach one photo (or
  reply to one) when you want an edit rather than a brand-new picture.
- If the bot seems stuck on an old topic, someone can run `/clear`.
- `/ask` works best for questions; `/imagine` is for pictures; `/voice` is when
  they want to hear a reply.
