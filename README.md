# DiscordBot

A Discord bot that integrates AI capabilities with server management and analytics. It provides natural conversations, art generation, data logging, and community engagement features.

## Features

### AI Integration (Grok)

- Natural language conversations using xAI's Grok API
- Function calling for real-world interactions:
  - Weather checks
  - Time zone information
  - Social media posting
  - Web searches

Example interaction:
![image](https://github.com/ajkeast/DiscordBot/assets/94143736/03f72ced-98a6-44bd-a016-38233738e049)

### Art Generation (Grok Imagine)

- AI-powered image creation from user descriptions
- Usage tracking and access management

Community-generated art examples:
![image](https://github.com/ajkeast/DiscordBot/assets/94143736/52c1b75e-b2c3-48da-9d13-0e295ab8f224)

### Server Analytics

- Comprehensive logging to MySQL database on AWS
- Custom dashboard for insights: https://peterdinklage.streamlit.app/
  - User activity metrics
  - Server usage patterns
  - Game leaderboards

Dashboard screenshots:
![image](https://user-images.githubusercontent.com/94143736/222931470-059e286e-43df-4cf8-b819-cc7c926436a1.png)
<img width="1255" alt="Screenshot 2023-03-04 at 5 39 41 PM" src="https://user-images.githubusercontent.com/94143736/222931762-c9b11440-763f-46c1-946a-c838852790dc.png">

- Server management commands:
  - Member information (`_members`)
  - Emoji usage (`_emojis`)
  - Channel management (`_channels`)

### Community Games

- Daily first-message competition with scoring system

Game interface:
<img width="427" alt="Screenshot 2023-03-04 at 5 28 37 PM" src="https://user-images.githubusercontent.com/94143736/222931682-32efca9a-26fe-4827-827a-75212db01b87.png">

## Technical Implementation

- Built with Python and Discord.py
- AI features via xAI's Grok and Grok Imagine APIs
- Data storage: MySQL on AWS
- Dashboard: Streamlit

Code organization:

- `cogs/ai.py`: AI functionality
- `cogs/server.py`: Server commands
- `cogs/first.py`: Game logic
- Additional modules in `cogs/` and `utils/`

## Future Development

- Integration of additional AI models
- Enhanced analytics features
- New community games
- Improved function calling capabilities

---

<p align="center">
Built by Alexander
</p>
