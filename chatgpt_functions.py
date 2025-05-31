import datetime, json, os           # core python libraries
import openai                       # chatGPT API
from dotenv import load_dotenv      # load .env
import pytz                         # timezones
import requests                     # http queries
import tweepy                       # twitter API
from bs4 import BeautifulSoup      # HTML parsing
import trafilatura                 # web content extraction

load_dotenv()

class FunctionRegistry:
    """Registry for all available functions that can be called by ChatGPT."""
    
    def __init__(self):
        """Initialize the registry with all available functions."""
        self.functions = {}
        self._register_all_functions()
    
    def _register_all_functions(self):
        """Register all available functions with their metadata."""
        # Date and Time Functions
        self._register_date_functions()
        
        # Weather Functions
        self._register_weather_functions()
        
        # Social Media Functions
        self._register_social_functions()

        # Search Functions
        self._register_search_functions()
    
    def _register_date_functions(self):
        """Register all date and time related functions."""
        self.register(
            name="get_todays_date",
            func=self._get_todays_date,
            description="Get the current date and time for a specific timezone",
            parameters={
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "Timezone identifier (e.g., 'US/Eastern', 'UTC', 'Europe/London')"
                    }
                },
                "required": ["timezone"]
            }
        )
    
    def _register_weather_functions(self):
        """Register all weather related functions."""
        self.register(
            name="get_current_weather",
            func=self._get_current_weather,
            description="Get current weather conditions for a specific location",
            parameters={
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City and state/country (e.g., 'San Francisco, CA', 'London, UK')"
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "Temperature unit preference"
                    }
                },
                "required": ["location"]
            }
        )
    
    def _register_social_functions(self):
        """Register all social media related functions."""
        self.register(
            name="post_tweet",
            func=self._post_tweet,
            description="Post a message to Twitter",
            parameters={
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Tweet content (max 280 characters)"
                    }
                },
                "required": ["message"]
            }
        )

    def _register_search_functions(self):
        """Register all search related functions."""
        self.register(
            name="brave_search",
            func=self._brave_search,
            description="Search the web using Brave Search API and optionally fetch page content",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (max 400 chars, 50 words)"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results to return (max 20)",
                        "default": 10
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Results offset for pagination (max 9)",
                        "default": 0
                    },
                    "result_filter": {
                        "type": "string",
                        "description": "Comma-delimited types to include (e.g., 'web,news,videos')",
                        "default": "web"
                    },
                    "freshness": {
                        "type": "string",
                        "description": "Filter by time (pd: 24h, pw: week, pm: month, py: year)",
                        "default": None
                    },
                    "fetch_content": {
                        "type": "boolean",
                        "description": "Whether to fetch and include the actual content of web pages",
                        "default": False
                    }
                },
                "required": ["query"]
            }
        )

    def register(self, name, func, description, parameters):
        """Register a new function with its metadata.
        
        Args:
            name (str): Unique identifier for the function
            func (callable): The actual function to be called
            description (str): Clear description of what the function does
            parameters (dict): JSONSchema object describing the function's parameters
        """
        self.functions[name] = {
            "func": func,
            "description": {
                "name": name,
                "description": description,
                "parameters": parameters
            }
        }
    
    @property
    def function_descriptions(self):
        """Get all function descriptions for ChatGPT API."""
        return [func["description"] for func in self.functions.values()]
    
    def execute(self, name, arguments):
        """Execute a registered function by name with given arguments.
        
        Args:
            name (str): Name of the function to execute
            arguments (str or dict): Function arguments as string or dict
            
        Returns:
            str: JSON string containing the function's response
            
        Raises:
            ValueError: If function name is not registered
        """
        if name not in self.functions:
            raise ValueError(f"Unknown function: {name}")
        
        try:
            parsed_args = json.loads(arguments) if isinstance(arguments, str) else arguments
            result = self.functions[name]["func"](**parsed_args)
            return json.dumps(result) if not isinstance(result, str) else result
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    def _get_todays_date(self, timezone='US/Eastern'):
        """Get the current date and time for a specific timezone.
        
        Args:
            timezone (str): Timezone identifier (e.g., 'US/Eastern', 'UTC')
            
        Returns:
            dict: Timezone and formatted datetime string
        """
        tz = pytz.timezone(timezone)
        return {
            "timezone": timezone,
            "today": str(datetime.datetime.now(tz))
        }

    def _get_current_weather(self, location, unit="fahrenheit"):
        """Get current weather conditions for a specific location.
        
        Args:
            location (str): City and state/country
            unit (str, optional): Temperature unit. Defaults to "fahrenheit"
            
        Returns:
            dict: Weather information including temperature, conditions, etc.
        """
        url = "https://weatherapi-com.p.rapidapi.com/current.json"
        headers = {
            "X-RapidAPI-Key": os.getenv('RAPID_API_KEY'),
            "X-RapidAPI-Host": "weatherapi-com.p.rapidapi.com"
        }
        
        response = requests.get(url, headers=headers, params={"q": location}).json()
        
        return {
            "location": response.get("location"),
            "unit": unit,
            "temperature": response.get("current").get("temp_f"),
            "conditions": response.get("current").get("condition").get("text"),
            "uv_level": response.get("current").get("uv"),
            "humidity": response.get("current").get("humidity"),
            "precip_inches": response.get("current").get("precip_in")
        }
    
    def _post_tweet(self, message):
        """Post a message to Twitter.
        
        Args:
            message (str): Tweet content (max 280 characters)
            
        Returns:
            dict: Tweet information including URL and status
        """
        try:
            twitter = tweepy.Client(
                consumer_key=os.getenv('TWITTER_API_KEY'),
                consumer_secret=os.getenv('TWITTER_API_KEY_SECRET'),
                access_token=os.getenv('TWITTER_ACCESS_TOKEN'),
                access_token_secret=os.getenv('TWITTER_ACCESS_TOKEN_SECRET')
            )
            
            tweet = twitter.create_tweet(text=message)
            tweet_id = tweet.data['id']
            
            return {
                "tweet_text": message,
                "tweet_url": f'https://twitter.com/twitter/statuses/{tweet_id}',
                "status": "success"
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

    def _fetch_url_content(self, url):
        """Fetch and extract meaningful content from a URL.
        
        Args:
            url (str): URL to fetch content from
            
        Returns:
            str: Extracted text content from the webpage
        """
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                content = trafilatura.extract(downloaded, include_links=False, include_images=False)
                if content:
                    return content.strip()
            
            response = requests.get(url, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for script in soup(["script", "style"]):
                script.decompose()
                
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)
            
            return text.strip()
            
        except Exception as e:
            return f"Error fetching content: {str(e)}"

    def _brave_search(self, query, count=10, offset=0, result_filter="web", freshness=None, fetch_content=False):
        """Perform a web search using Brave Search API and optionally fetch page content.
        
        Args:
            query (str): Search query (max 400 chars, 50 words)
            count (int, optional): Number of results. Defaults to 10.
            offset (int, optional): Results offset. Defaults to 0.
            result_filter (str, optional): Result types to include. Defaults to "web".
            freshness (str, optional): Time filter. Defaults to None.
            fetch_content (bool, optional): Whether to fetch and include actual page content. Defaults to False.
            
        Returns:
            dict: Search results from Brave API, optionally including page content
        """
        url = "https://api.search.brave.com/res/v1/web/search"
        
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": os.getenv('BRAVE_API_KEY')
        }
        
        params = {
            "q": query,
            "count": min(count, 20),  # Ensure count doesn't exceed max
            "offset": min(offset, 9),  # Ensure offset doesn't exceed max
            "result_filter": result_filter
        }
        
        if freshness:
            params["freshness"] = freshness
            
        try:
            response = requests.get(
                url,
                headers=headers,
                params=params
            )
            response.raise_for_status()
            results = response.json()
            
            if fetch_content and 'web' in result_filter:
                if 'web' in results and 'results' in results['web']:
                    for result in results['web']['results']:
                        result['page_content'] = self._fetch_url_content(result['url'])
            
            return results
            
        except requests.exceptions.RequestException as e:
            return {
                "status": "error",
                "error": str(e)
            }

class ChatGPTClient:
    """Client for interacting with ChatGPT API."""
    
    def __init__(self, api_key=None):
        self.client = openai.OpenAI(api_key=api_key or os.getenv('CHAT_API_KEY'))
        self.function_registry = FunctionRegistry()
        self.model = "gpt-4.1-mini"  # Store model name as instance variable
    
    def call_chatgpt(self, chat_history, prompt, max_history=20, max_tokens=512, user_id=None, image_urls=None):
        """Call ChatGPT API with function calling support.
        
        Args:
            chat_history (list): List of previous messages
            prompt (str or dict): User's prompt or full message object
            max_history (int, optional): Maximum number of messages to keep. Defaults to 20.
            max_tokens (int, optional): Maximum tokens in response. Defaults to 512.
            user_id (int, optional): Discord user ID for logging. Defaults to None.
            image_urls (list, optional): List of image URLs to include. Defaults to None.
        """
        try:
            # Handle prompt whether it's a string or message object
            if isinstance(prompt, str):
                message = {"role": "user", "content": prompt}
            else:
                message = prompt

            # Add image URLs if present
            if image_urls:
                message["content"] = [
                    {"type": "text", "text": message["content"]},
                    *[{"type": "image_url", "image_url": url} for url in image_urls]
                ]
            self._append_and_shift(chat_history, message, max_history)
            
            while True:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.7,
                    max_tokens=max_tokens,
                    messages=chat_history,
                    functions=self.function_registry.function_descriptions,
                    function_call="auto"
                )
                
                message = response.choices[0].message
                
                # Log the interaction if user_id is provided
                if user_id is not None:
                    from utils.db import db_ops
                    function_calls = []
                    if message.function_call:
                        function_calls.append({
                            "name": message.function_call.name,
                            "arguments": message.function_call.arguments
                        })
                    
                    db_ops.log_chatgpt_interaction(
                        user_id=user_id,
                        model=self.model,
                        request_messages=chat_history,
                        response_content=message.content,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                        function_calls=function_calls if function_calls else None,
                        image_urls=image_urls
                    )
                
                # If no function call, return the response
                if response.choices[0].finish_reason != "function_call":
                    self._append_and_shift(chat_history, {
                        "role": "assistant",
                        "content": message.content
                    }, max_history)
                    return chat_history, message.content[:2000]
                
                # Handle function call
                function_name = message.function_call.name
                function_response = self.function_registry.execute(
                    function_name,
                    message.function_call.arguments
                )
                
                self._append_and_shift(chat_history, {
                    "role": "function",
                    "name": function_name,
                    "content": function_response
                }, max_history)
                
        except Exception as e:
            return chat_history, f'Error: {str(e)}'
    
    @staticmethod
    def _append_and_shift(arr, item, max_len):
        """Append an item to array and maintain maximum length."""
        arr.append(item)
        if len(arr) > max_len:
            arr.pop(1)  # Keep system message, remove oldest message

def call_dalle3(prompt):
    """Generate an image using DALL-E 3."""
    try:
        client = openai.OpenAI(api_key=os.getenv('CHAT_API_KEY'))
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024"
        )
        return response.data[0].url
    except Exception as e:
        return f'Error generating image: {str(e)}'