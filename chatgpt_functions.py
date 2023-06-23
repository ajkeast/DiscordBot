import datetime, json, os
import openai                                               # ChatGPT API
from dotenv import load_dotenv                              # Load .env

load_dotenv()
openai.api_key = os.getenv('CHAT_API_KEY')

def call_chatGPT(chat_history, prompt):
    """call ChatGPT API and with error handeling blocks"""
    # try:
    append_and_shift(chat_history,{"role": "user", "content": prompt},max_len=10)
    response = openai.ChatCompletion.create(model="gpt-3.5-turbo-0613",
                                            temperature=0.7,
                                            messages=chat_history,
                                            functions=function_descriptions,
                                            function_call="auto")
    
    if response["choices"][0]["finish_reason"] != "function_call":
        append_and_shift(chat_history,{"role": "assistant", "content": response['choices'][0]['message']['content']},max_len=10)
    
    # If there was a function call, append it to the message history and 
    while response["choices"][0]["finish_reason"] == "function_call":
        function_response = function_call(response)
        append_and_shift(chat_history,{"role": "function","name": response["choices"][0]["message"]["function_call"]["name"],"content": json.dumps(function_response)},max_len=10)
        response = openai.ChatCompletion.create(model="gpt-3.5-turbo-0613",
                                                temperature=0.7,
                                                messages=chat_history,
                                                functions=function_descriptions,
                                                function_call="auto")   
    return chat_history, response['choices'][0]['message']['content'][:2000] # limited to 2000 characters for discord
    #except Exception as e:
    #    return f'Looks like there was an error: {e}'
    
def function_call(ai_response):
    function_call = ai_response["choices"][0]["message"]["function_call"]
    function_name = function_call["name"]
    arguments = function_call["arguments"]
    if function_name == "get_todays_date":
        return get_todays_date()
    else:
        return

function_descriptions = [
    {
        "name": "get_todays_date",
        "description": "Get todays date, returned as a string in format of yyyy-mm-dd hh:mm:ss",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type":"string", "description":"location where a common standard time is used "}
            },
        },
    }
]

def get_todays_date(timezone='Eastern'):
    """Get the current date"""
    today = {
        "timezone": timezone,
        "today": str(datetime.datetime.today())
    }
    return json.dumps(today)

def append_and_shift(arr, v, max_len):
    """
    Append a value to an array up to a set maximum length.
    If the maximum length is reached, shift out the second earliest entry.
    """
    arr.append(v)
    if len(arr) > max_len:
        arr.pop(1)

# whitelist of users who can use ChatGPT
IDCARD = ['162725160397438978','94235023560941568','95321829031280640','94254577766891520','250729999349317632','186667084007211008']