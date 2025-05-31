# DiscordBot

Hey there! 👋 I'd like to introduce you to my Discord bot project that combines the power of AI with server management and analytics. What started as a simple chatbot has grown into something I'm really proud of - a full-featured assistant that helps manage our server, generates AI art, and even keeps track of our community games!

## The AI Brain 🤖

### ChatGPT Integration

The heart of this bot is its ability to have natural conversations using ChatGPT. I designed it with a fun twist - it talks like a chill, surfer bro who's always relaxed! This personality choice has made interactions with the bot not just helpful, but genuinely entertaining for our server members.

What makes it really special is how it can interact with real-world data. I implemented a function calling system that lets the bot do things like:

- Check the current weather for any location
- Tell you the time in different timezones
- Even post tweets (when it's feeling social!)

Here's what it looks like in action:
![image](https://github.com/ajkeast/DiscordBot/assets/94143736/03f72ced-98a6-44bd-a016-38233738e049)

### DALL·E 3 Art Studio

One of my favorite features is the AI art generation using DALL·E 3. Members can describe any image they imagine, and the bot brings it to life in stunning 1024x1024 resolution. I've added some smart features here too - like tracking usage and managing access to keep costs under control (those API calls aren't free, after all! 😅).

Check out some of the amazing art our community has created:
![image](https://github.com/ajkeast/DiscordBot/assets/94143736/52c1b75e-b2c3-48da-9d13-0e295ab8f224)

## Server Management & Analytics 📊

I'm a bit of a data nerd, so I wanted to know more about how our server was being used. That's why I built a comprehensive logging system that writes everything to a MySQL database hosted on AWS. This has turned into one of the most valuable parts of the project!

### The Dashboard

All this data comes to life in our custom dashboard (https://peterdinklage.streamlit.app/). It's where you can see things like:

- Who's been most active this month
- Popular times for server activity
- And my personal favorite - who's winning our daily games!

Here's what the dashboard looks like:
![image](https://user-images.githubusercontent.com/94143736/222931470-059e286e-43df-4cf8-b819-cc7c926436a1.png)
<img width="1255" alt="Screenshot 2023-03-04 at 5 39 41 PM" src="https://user-images.githubusercontent.com/94143736/222931762-c9b11440-763f-46c1-946a-c838852790dc.png">

### Behind the Scenes

The bot quietly keeps track of everything happening in the server. I've built commands that help manage:

- Member information (`_members`)
- Custom emoji usage (`_emojis`)
- Channel management (`_channels`)

These commands help keep our server organized and give us insights into how it's growing over time.

## Community Games 🎮

Remember that daily game I mentioned? It's become one of our server's most popular features! Members compete to send the first message of the day, and the bot keeps track of everyone's score. It's amazing how such a simple game has created such engagement - we've got members setting alarms for midnight just to try to win! 😄

Here's what our first message competition looks like:
<img width="427" alt="Screenshot 2023-03-04 at 5 28 37 PM" src="https://user-images.githubusercontent.com/94143736/222931682-32efca9a-26fe-4827-827a-75212db01b87.png">

## Technical Stuff (For The Curious) 🛠️

If you're interested in the technical side, here's how it all works:

- The core bot is built with Python and Discord.py
- AI features use OpenAI's GPT-4 and DALL·E 3 APIs
- All the data is stored in a MySQL database on AWS
- The dashboard is built with Streamlit

The code is organized into different modules (in the `cogs` folder) to keep things clean and maintainable. Each feature has its own space:

- `ai.py` handles all the AI magic
- `server.py` manages server-related commands
- `first.py` runs our beloved first message game
- And more!

## What's Next? 🚀

I'm constantly working on making the bot better! Some things I'm excited about:

- Exploring new AI models to add more features
- Expanding our analytics capabilities
- Adding more community games
- Making the function calling system even smarter

## The Impact 💫

What started as a fun project has turned into a vital part of our server. It's helped create a more engaged community, provided valuable insights through analytics, and shown me the practical applications of modern AI technologies. Plus, it's been an amazing learning experience in working with APIs, managing databases, and building user-friendly features.

---

<p align="center">
Built with ❤️ by Alexander
</p>

Want to know more about any part of the project? Feel free to reach out! I love talking about it and sharing what I've learned along the way.
