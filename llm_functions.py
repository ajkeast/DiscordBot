import datetime, json, os           # core python libraries
import openai                       # chatGPT API
from dotenv import load_dotenv      # load .env
import pytz                         # timezones
import requests                     # http queries
import tweepy                       # twitter API
from bs4 import BeautifulSoup      # HTML parsing
import trafilatura                 # web content extraction
import time                         # for rate limiting
from collections import deque       # for rate limiting
from threading import Lock          # for thread safety

load_dotenv()

class RateLimiter:
    """Rate limiter for API calls."""
    
    def __init__(self, max_calls, time_window):
        """Initialize rate limiter.
        
        Args:
            max_calls (int): Maximum number of calls allowed in time window
            time_window (int): Time window in seconds
        """
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = deque()
        self.lock = Lock()
    
    def acquire(self):
        """Acquire permission to make an API call.
        
        Returns:
            bool: True if call is allowed, False otherwise
        """
        with self.lock:
            now = time.time()
            
            # Remove old calls
            while self.calls and now - self.calls[0] > self.time_window:
                self.calls.popleft()
            
            if len(self.calls) < self.max_calls:
                self.calls.append(now)
                return True
            return False
    
    def wait(self):
        """Wait until a call can be made."""
        while not self.acquire():
            time.sleep(0.1)

class FunctionRegistry:
    """Registry for all available functions that can be called by Grok."""
    
    def __init__(self):
        """Initialize the registry with all available functions."""
        self.functions = {}
        self._register_all_functions()
        # Initialize rate limiters
        self.brave_search_limiter = RateLimiter(max_calls=10, time_window=60)  # 10 calls per minute
        self.brave_image_limiter = RateLimiter(max_calls=10, time_window=60)   # 10 calls per minute
    
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
            },
            tool_type="function"
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
            },
            tool_type="function"
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
            },
            tool_type="function"
        )

        self.register(
            name="brave_image_search",
            func=self._brave_image_search,
            description="Search for images using Brave Search API",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (max 400 chars, 50 words)"
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of results to return (max 100)",
                        "default": 10
                    },
                    "country": {
                        "type": "string",
                        "description": "2-character country code (e.g., 'US', 'UK')",
                        "default": "US"
                    },
                    "search_lang": {
                        "type": "string",
                        "description": "2-character language code (e.g., 'en', 'es')",
                        "default": "en"
                    },
                    "safesearch": {
                        "type": "string",
                        "enum": ["off", "strict"],
                        "description": "Filter adult content",
                        "default": "strict"
                    },
                    "spellcheck": {
                        "type": "boolean",
                        "description": "Whether to spellcheck the query",
                        "default": True
                    }
                },
                "required": ["query"]
            },
            tool_type="function"
        )



    def register(self, name, func, description, parameters, tool_type="function"):
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
        """Get all function descriptions for Grok API."""
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
                "tweet_url": f'https://x.com/i/status/{tweet_id}',
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
            
        Raises:
            ValueError: If URL is invalid
            requests.exceptions.RequestException: If request fails
        """
        # Validate URL
        if not url or not isinstance(url, str):
            raise ValueError("Invalid URL provided")
            
        if not url.startswith(('http://', 'https://')):
            raise ValueError("URL must start with http:// or https://")
            
        try:
            # First try with trafilatura
            downloaded = trafilatura.fetch_url(url, timeout=10)
            if downloaded:
                content = trafilatura.extract(downloaded, include_links=False, include_images=False)
                if content:
                    return content.strip()
            
            # Fallback to requests + BeautifulSoup
            response = requests.get(url, timeout=10, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            })
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove unwanted elements
            for element in soup(['script', 'style', 'nav', 'footer', 'header']):
                element.decompose()
                
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)
            
            return text.strip()
            
        except requests.exceptions.Timeout:
            raise requests.exceptions.RequestException("Request timed out")
        except requests.exceptions.HTTPError as e:
            raise requests.exceptions.RequestException(f"HTTP error occurred: {str(e)}")
        except requests.exceptions.ConnectionError:
            raise requests.exceptions.RequestException("Failed to connect to the server")
        except Exception as e:
            raise requests.exceptions.RequestException(f"Error fetching content: {str(e)}")

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
        # Wait for rate limit
        self.brave_search_limiter.wait()
        
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

    def _brave_image_search(self, query, count=5, country="US", search_lang="en", safesearch="off", spellcheck=True):
        """Perform an image search using Brave Search API.
        
        Args:
            query (str): Search query (max 400 chars, 50 words)
            count (int, optional): Number of results. Defaults to 5.
            country (str, optional): 2-character country code. Defaults to "US".
            search_lang (str, optional): 2-character language code. Defaults to "en".
            safesearch (str, optional): Filter adult content. Defaults to "strict".
            spellcheck (bool, optional): Whether to spellcheck query. Defaults to True.
            
        Returns:
            dict: Image search results from Brave API
        """
        # Wait for rate limit
        self.brave_image_limiter.wait()
        
        url = "https://api.search.brave.com/res/v1/images/search"
        
        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": os.getenv('BRAVE_API_KEY')
        }
        
        params = {
            "q": query,
            "count": min(count, 100),  # Ensure count doesn't exceed max
            "country": country,
            "search_lang": search_lang,
            "safesearch": safesearch,
            "spellcheck": spellcheck
        }
            
        try:
            response = requests.get(
                url,
                headers=headers,
                params=params
            )
            response.raise_for_status()
            results = response.json()
            
            # Update the results to use the direct image URL
            if 'results' in results:
                for result in results['results']:
                    if 'properties' in result and 'url' in result['properties']:
                        result['url'] = result['properties']['url']
            
            return results
            
        except requests.exceptions.RequestException as e:
            return {
                "status": "error",
                "error": str(e)
            }


class GrokClient:
    """Client for interacting with Grok API."""
    
    def __init__(self, api_key=None):
        # Configure for Grok API - compatible with OpenAI SDK
        self.client = openai.OpenAI(
            api_key=api_key or os.getenv('XAI_API_KEY'),
            base_url="https://api.x.ai/v1"
        )
        self.function_registry = FunctionRegistry()
        self.model = "grok-3-mini"  # Use Grok model
    
    def call_grok(self, chat_history, prompt, max_history=20, max_tokens=512, user_id=None, image_urls=None):
        """Call Grok API with function calling and vision support.
        
        Args:
            chat_history (list): List of previous messages
            prompt (str): User's prompt
            max_history (int, optional): Maximum number of messages to keep. Defaults to 20.
            max_tokens (int, optional): Maximum tokens in response. Defaults to 512.
            user_id (int, optional): Discord user ID for logging. Defaults to None.
            image_urls (list, optional): List of image URLs to include. Defaults to None.
        """
        try:
            # Prepare message content based on whether there are images
            if image_urls:
                message_content = [
                    {"type": "text", "text": prompt},
                    *[{"type": "image_url", "image_url": {"url": url}} for url in image_urls]
                ]
            else:
                message_content = prompt

            # Append user prompt and maintain history length
            message = {"role": "user", "content": message_content}
            self._append_and_shift(chat_history, message, max_history)
            
            while True:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=0.7,
                    max_tokens=max_tokens,
                    messages=chat_history,
                    tools=[func["description"] for func in self.function_registry.functions.values()],
                    tool_choice="auto"
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
    """Generate an image using DALL-E 3.
    
    Note: This function still uses OpenAI's DALL-E 3 API since Grok does not support image generation.
    
    Args:
        prompt (str): The image generation prompt
        
    Returns:
        dict: A dictionary containing the image URL and any error information
    """
    try:
        client = openai.OpenAI(api_key=os.getenv('CHAT_API_KEY'))
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1
        )
        
        return {
            "status": "success",
            "image_url": response.data[0].url,
            "revised_prompt": response.data[0].revised_prompt
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }