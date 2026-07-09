"""Unit tests for call_grok_imagine image input routing."""

from unittest.mock import MagicMock, patch

from chatgpt_functions import call_grok_imagine
from tests.reporting import SECTION_COMMANDS


@patch("chatgpt_functions.Client")
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
def test_call_grok_imagine_multiple_images_uses_image_urls(mock_client_cls, report):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.image.sample.return_value = MagicMock(image=b"jpeg")
    urls = [
        "https://cdn.example.com/a.png",
        "https://cdn.example.com/b.png",
        "https://cdn.example.com/c.png",
    ]

    result = call_grok_imagine(
        "Combine <IMAGE_0> and <IMAGE_1> with style from <IMAGE_2>",
        input_image_urls=urls,
    )

    kwargs = mock_client.image.sample.call_args.kwargs
    report.record("image_urls count", 3, len(kwargs.get("image_urls") or []), section=SECTION_COMMANDS)
    assert kwargs["image_urls"] == urls
    assert "image_url" not in kwargs
    assert result["status"] == "success"


@patch("chatgpt_functions.logger")
@patch("chatgpt_functions.Client")
def test_call_grok_imagine_logs_exceptions(mock_client_cls, mock_logger, report):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.image.sample.side_effect = RuntimeError("api down")

    result = call_grok_imagine("a cat")

    report.record("status", "error", result["status"], section=SECTION_COMMANDS)
    report.record("error", "api down", result["error"], section=SECTION_COMMANDS)
    assert result == {"status": "error", "error": "api down"}
    mock_logger.exception.assert_called_once_with("Grok Imagine failed")
