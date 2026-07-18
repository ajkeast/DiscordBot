# The "First" Game

The most popular game on the server: be the first person to claim the day.

## How to play

- Type `/1st` in **#general**. It won't work in other channels — the bot will
  tell you to go to #general.
- Only **one person** wins each day. A new day starts at **midnight US/Eastern**.
- If you win, the bot announces it and you earn **1 DINK** (see the `dinkcoin` topic).
  For now the win message also promotes the recently launched site at
  https://dinkscord.com with a link button.
- If someone already claimed today, the bot says first has been taken — try again
  tomorrow.

## Stats and scores

- **Score** — how many daily wins you have total. `/score` shows the top 5
  players plus the most recent winner and their current streak.
- **Streak** — how many days in a row the same person has won first. `/stats`
  (optionally pick a user) shows a player's total wins, juice, and their longest
  ever streak.
- **Juice 🧃** — a lateness score where **more juice is better**. Each win earns
  the number of **minutes past midnight Eastern** when you claimed (e.g. 12:07am
  = 7 juice; 11:30pm = 1410). The strategy is to **wait as long as possible**
  before typing `/1st` — claim late in the day to maximize your juice. Early
  claims earn less; late claims earn more.
- If nobody claims for a full day, the next winner also picks up 1440 juice per
  missed day on top of their within-day minutes.
- **Total juice** (sum across all your wins) is what `/juice` ranks — the
  leaderboard goes to whoever has accumulated the most juice over time.
- `/juice` also shows the single-day high score (most juice earned in one claim).

## Commands

| Command | What it does |
|---------|--------------|
| `/1st` | Claim first for today (#general only, once per day) |
| `/score` | Top 5 wins + most recent winner and streak |
| `/stats [user]` | One player's wins, juice, and longest streak |
| `/juice` | Top 5 juice + single-day high score |
| `/graph` | Chart of cumulative firsts over time |
| `/juicegraph` | Chart of daily juice over time |
