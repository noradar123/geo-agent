from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from geoagent.demo import DemoVision
from geoagent.models import LocationGuess
from geoagent.vision import Vision


def test_image_input_and_structured_parsing(tmp_path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (20, 20), "green").save(image)
    guess = DemoVision().locate([image], tmp_path)
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        id="test-response",
        model="test-model",
        status="completed",
        usage=None,
        output=[],
        output_parsed=guess,
    )
    actual = Vision(client, "test-model").locate([image], tmp_path)
    assert actual.latitude == guess.latitude
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["text_format"] is LocationGuess
    assert kwargs["store"] is False
    assert kwargs["input"][0]["content"][1]["image_url"].startswith("data:image/png;base64,")
    assert (tmp_path / "location_api.json").exists()


def test_refusal_does_not_return_prediction(tmp_path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (20, 20)).save(image)
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        id="refused",
        model="test-model",
        status="completed",
        usage=None,
        output=[],
        output_parsed=None,
    )
    with pytest.raises(RuntimeError, match="no usable structured output"):
        Vision(client).locate([image], tmp_path)


def test_non_png_is_rejected_before_api_call(tmp_path):
    image = tmp_path / "bad.png"
    image.write_text("not a screenshot")
    client = Mock()
    with pytest.raises(ValueError, match="expected a PNG"):
        Vision(client).locate([image], tmp_path)
    client.responses.parse.assert_not_called()
