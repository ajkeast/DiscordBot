import datetime, json, os           # core python libraries
import openai                       # chatGPT API
from dotenv import load_dotenv      # load .env
import pytz                         # timezones
import requests                     # http queries

load_dotenv()
openai.api_key = os.getenv('CHAT_API_KEY')

def call_chatGPT(chat_history, prompt):
    """Call ChatGPT API with error handling blocks.
    
    This function interacts with the ChatGPT API to generate responses based on the given chat history
    and a prompt. It appends the prompt to the chat history, sends the request to the API, and processes
    the response. If there is a function call in the response, it appends the function response to the chat
    history and sends the updated history again. The function returns the updated chat history and the
    generated response.
    
    Args:
        chat_history (list of dict): List of dictionaries representing the chat history.
        prompt (str): The user prompt to be sent to the ChatGPT API.
    
    Returns:
        tuple: A tuple containing the updated chat history and the generated response.
            chat_history (list): Updated chat history after appending new messages.
            response_content (str): The generated response content limited to 2000 characters.
    """
    
    try:
        # Append the user prompt to the chat history
        append_and_shift(chat_history, {"role": "user", "content": prompt}, max_len=10)
        
        # Send request to the ChatGPT API
        response = openai.ChatCompletion.create(model="gpt-3.5-turbo-0613",
                                                temperature=0.7,
                                                messages=chat_history,
                                                functions=function_descriptions,
                                                function_call="auto")
        
        # If the response is not a function call, append assistant's response to the chat history
        if response["choices"][0]["finish_reason"] != "function_call":
            append_and_shift(chat_history, {"role": "assistant", "content": response['choices'][0]['message']['content']}, max_len=10)
        
        # If there was a function call, append it to the message history and run the response again
        while response["choices"][0]["finish_reason"] == "function_call":
            function_response = function_call(response)
            append_and_shift(chat_history, {"role": "function", "name": response["choices"][0]["message"]["function_call"]["name"], "content": json.dumps(function_response)}, max_len=10)
            response = openai.ChatCompletion.create(model="gpt-3.5-turbo-0613",
                                                    temperature=0.7,
                                                    messages=chat_history,
                                                    functions=function_descriptions,
                                                    function_call="auto")
        
        # Return the updated chat history and the generated response content (limited to 2000 characters)
        return chat_history, response['choices'][0]['message']['content'][:2000]
    
    except Exception as e:
        # Handle any exceptions by returning an error message
        return chat_history, f'Looks like there was an error: {repr(e)}'

    
def function_call(ai_response):
    """Process the function call in the AI response and invoke the corresponding function.
    
    This function extracts the function call details from the AI response and invokes the appropriate
    function based on the function name. The function call typically includes the function name and its
    corresponding arguments. The supported functions in this implementation are:
    - get_todays_date: Retrieves today's date based on the provided timezone.
    - get_current_weather: Retrieves the current weather based on the provided location.
    - get_minecraft_server: Retrieves information about a Minecraft server based on the provided IP address.
    
    Args:
        ai_response (json): The AI response containing the function call details.
    
    Returns:
        The result of the invoked function (must be json) or None if the function name is not supported.
    """
    
    # Extract function call details from the AI response
    function_call = ai_response["choices"][0]["message"]["function_call"]
    function_name = function_call["name"]
    arguments = function_call["arguments"]
    
    # Process the function call based on the function name
    if function_name == "get_todays_date":
        # Extract the timezone argument and invoke the get_todays_date function
        timezone = eval(arguments).get("timezone")
        return get_todays_date(timezone)
    elif function_name == "get_current_weather":
        # Extract the location argument and invoke the get_current_weather function
        location = eval(arguments).get("location")
        return get_current_weather(location)
    elif function_name == "get_minecraft_server":
        # Extract the IP address argument and invoke the get_minecraft_server function
        ip_address = eval(arguments).get("ip_address")
        return get_minecraft_server(ip_address)
    else:
        # If the function name is not supported, return None
        return None


# .json of all functions & arguments with descriptions so the model can intelligently decide when and how to invoke
function_descriptions = [
    {
        "name": "get_todays_date",
        "description": "Get todays date, returned as a string in format of yyyy-mm-dd hh:mm:ss",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type":"string", "description":"location where a common standard time is used. Default to US/Eastern if not specified"}
            },
             "required": ["timezone"]
        },
    },
    {
        "name": "get_current_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city and state, ex. San Francisco, CA. Default is Boston, MA when not specified.",
                },
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["location"],
        },
    },
    {
        "name": "get_minecraft_server",
        "description": "Get the number of players online and the online status for the minecraft server",
        "parameters": {
            "type": "object",
            "properties": {
                "ip_address": {
                    "type": "string",
                    "description": "ip address string",
                },
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["ip_address"]
        },
    }
]

def get_todays_date(timezone='US/Eastern'):
    """Get the current date and time based on the timezone"""
    tz = pytz.timezone(timezone)
    today = {
        "timezone": timezone,
        "today": str(datetime.datetime.now(tz))
    }
    return json.dumps(today)


def get_current_weather(location, unit="fahrenheit"):
    """Get the current weather in a given location"""

    url = "https://weatherapi-com.p.rapidapi.com/current.json"
    querystring = {"q":location}
    headers = {"X-RapidAPI-Key": "d66e36c641msh71bd179143810dep11f9f8jsn691562db2764",
               "X-RapidAPI-Host": "weatherapi-com.p.rapidapi.com"}
    response = requests.get(url, headers=headers, params=querystring).json()

    weather = {"location":response.get("location"),
               "unit":unit,
               "temperature":response.get("current").get("temp_f"),
               "conditions":response.get("current").get("condition").get("text"),
               "uv level":response.get("current").get("uv"),
               "humidity":response.get("current").get("humidity"),
               "precip_inches":response.get("current").get("precip_in")
    }
    print(response)

    return json.dumps(weather)

def get_minecraft_server(ip_address='51.81.151.253:25583'):
    """Get the server details based on the IP address """
    
    url = "https://minecraft-server-status1.p.rapidapi.com/servers/single/lite"

    payload = { "host": ip_address }
    headers = {
        "content-type": "application/json",
        "X-RapidAPI-Key": "d66e36c641msh71bd179143810dep11f9f8jsn691562db2764",
        "X-RapidAPI-Host": "minecraft-server-status1.p.rapidapi.com"
    }

    response = requests.post(url, json=payload, headers=headers)
    print(response.json)

    return response.json()


def append_and_shift(arr, v, max_len):
    """
    Append a value (v) to an array (arr) up to a set maximum length (max_len).
    If the maximum length is reached, shift out the second earliest registry.
    """
    arr.append(v)
    if len(arr) > max_len:
        arr.pop(1)

# whitelist of users who can use ChatGPT
IDCARD = ['162725160397438978','94235023560941568','95321829031280640','94254577766891520','250729999349317632','186667084007211008']