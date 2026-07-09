"""Unit tests for call_grok_imagine image input routing."""

import base64
from unittest.mock import MagicMock, patch

from chatgpt_functions import call_grok_imagine
from tests.reporting import SECTION_COMMANDS


@patch("chatgpt_functions.Client")
@patch.dict("os.environ", {"XAI_API_KEY": "test-key"})
def test_call_grok_imagine_no_images(mock_client_cls, report):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.image.sample.return_value = MagicMock(image=b"jpeg")

    result = call_grok_imagine("a cat")

    kwargs = mock_client.image.sample.call_args.kwargs
    report.record("image_url", None, kwargs.get("image_url"), section=SECTION_COMMANDS)
    report.record("image_urls", None, kwargs.get("image_urls"), section=SECTION_COMMANDS)
    assert "image_url" not in kwargs
    assert "image_urls" not in kwargs
    assert result["status"] == "success"


@patch("chatgpt_functions.Client")
@patch.dict("os.environ", {"XAI_API_KEY": "test-key"})
def test_call_grok_imagine_single_image_uses_image_url(mock_client_cls, report):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.image.sample.return_value = MagicMock(image=b"jpeg")
    url = "https://cdn.example.com/a.png"

    result = call_grok_imagine("edit this", input_image_urls=[url])

    kwargs = mock_client.image.sample.call_args.kwargs
    report.record("image_url", url, kwargs.get("image_url"), section=SECTION_COMMANDS)
    assert kwargs["image_url"] == url
    assert "image_urls" not in kwargs
    assert result["status"] == "success"


@patch("chatgpt_functions.Client")
@patch("chatgpt_functions.requests.post")
@patch.dict("os.environ", {"XAI_API_KEY": "test-key"})
def test_call_grok_imagine_multiple_images_uses_rest_edits(mock_post, mock_client_cls, report):
    urls = [
        "https://cdn.example.com/a.png",
        "https://cdn.example.com/b.png",
        "https://cdn.example.com/c.png",
    ]
    mock_response = MagicMock()
    mock_response.ok = True
    mock_response.json.return_value = {
        "data": [{"b64_json": base64.b64encode(b"multi-jpeg").decode("ascii")}]
    }
    mock_post.return_value = mock_response

    result = call_grok_imagine(
        "Combine <IMAGE_0> and <IMAGE_1> with style from <IMAGE_2>",
        input_image_urls=urls,
    )

    mock_client_cls.assert_not_called()
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    report.record("rest images count", 3, len(payload["images"]), section=SECTION_COMMANDS)
    report.record("response_format", "b64_json", payload.get("response_format"), section=SECTION_COMMANDS)
    assert payload["images"] == [{"url": u, "type": "image_url"} for u in urls]
    assert result["status"] == "success"
    assert result["image_bytes"] == b"multi-jpeg"
