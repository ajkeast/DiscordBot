# DiscordBot
Discord bot with ChatGPT API and SQL database for logging server events.

## Features
### Chat GPT
My Discord bot is designed to answer questions directly in chat using the ChatGPT API. This means that users can ask the bot any question they have, and the bot will respond with a relevant answer generated by the ChatGPT model. This is a great way to provide instant assistance and support to users, as they can get their questions answered quickly and easily.

<img width="1019" alt="Screenshot 2023-03-04 at 5 14 24 PM" src="https://user-images.githubusercontent.com/94143736/222931031-924ab917-49d8-4c2e-947d-0c91d6f833f7.png">

# Server Dashboard
In addition to answering questions, the bot also writes events with usernames and timestamps to a MySQL database hosted on Amazon Web Services (AWS). This means that you can keep track of when users are interacting with your bot, and what commands they are sending. This information can be used to improve the performance and effectiveness of the bot over time, by analyzing the data to identify trends and patterns in user behavior. 

Basic summaries of the data can be found in the server dashboard (https://peterdinklage.streamlit.app/) displaying information like monthly messages by user or the number of times a user has had the 1st message of the day. 

![image](https://user-images.githubusercontent.com/94143736/222931470-059e286e-43df-4cf8-b819-cc7c926436a1.png)

# Games
Here it's being used to log and record who sends the first message of the day. This has become one of the most popular games on our server.

<img width="427" alt="Screenshot 2023-03-04 at 5 28 37 PM" src="https://user-images.githubusercontent.com/94143736/222931682-32efca9a-26fe-4827-827a-75212db01b87.png">
