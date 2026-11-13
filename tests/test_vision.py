import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

from geoagent.demo import DemoVision
from geoagent.models import GridMapTarget, LocationGuess, MapAnchor
from geoagent.vision import Vision, draw_grid, fit_anchors, grid_to_map_target


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
    actual = Vision(client, "test-model", "openai").locate([image], tmp_path)
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
        Vision(client, provider="openai").locate([image], tmp_path)


def test_non_png_is_rejected_before_api_call(tmp_path):
    image = tmp_path / "bad.png"
    image.write_text("not a screenshot")
    client = Mock()
    with pytest.raises(ValueError, match="expected a PNG"):
        Vision(client, provider="openai").locate([image], tmp_path)
    client.responses.parse.assert_not_called()


def grid(cell="C4", x=0.5, y=0.25, visible=True):
    return GridMapTarget(
        anchors=[],
        reasoning="test",
        visible=visible,
        cell=cell,
        x_in_cell=x,
        y_in_cell=y,
        confidence=0.8,
    )


def test_grid_cell_converts_to_clean_image_fractions():
    target = grid_to_map_target(grid("C4", 0.5, 0.25), 8, 6)
    assert target.visible
    assert target.x_fraction == pytest.approx((2 + 0.5) / 8)
    assert target.y_fraction == pytest.approx((3 + 0.25) / 6)
    assert target.confidence == 0.8


@pytest.mark.parametrize("cell", ["Z1", "A0", "A7", "4C", ""])
def test_invalid_grid_cell_is_not_visible(cell):
    assert not grid_to_map_target(grid(cell), 8, 6).visible


def test_grid_image_adds_label_margin(tmp_path):
    image = tmp_path / "map.png"
    Image.new("RGB", (864, 630), "blue").save(image)
    draw_grid(image, tmp_path / "grid.png")
    with Image.open(tmp_path / "grid.png") as result:
        assert result.size == (864 + 30, 630 + 26)


def test_map_target_sends_clean_and_gridded_images(tmp_path):
    image = tmp_path / "map_0.png"
    Image.new("RGB", (80, 60), "white").save(image)
    guess = DemoVision().locate([image], tmp_path)
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        id="r", model="m", status="completed", usage=None, output=[], output_parsed=grid()
    )
    target = Vision(client, "test-model", "openai").map_target(image, guess, tmp_path, "x.json")
    content = client.responses.parse.call_args.kwargs["input"][0]["content"]
    assert [part["type"] for part in content] == ["input_text", "input_image", "input_image"]
    assert client.responses.parse.call_args.kwargs["text_format"] is GridMapTarget
    assert (tmp_path / "map_0_grid.png").exists() and (tmp_path / "x.json").exists()
    assert target.x_fraction == pytest.approx(2.5 / 8)


def test_anthropic_request_shape(tmp_path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (20, 20), "green").save(image)
    guess = DemoVision().locate([image], tmp_path)
    client = Mock()
    client.beta.messages.parse.return_value = SimpleNamespace(
        id="msg",
        model="claude-opus-5-5",
        stop_reason="end_turn",
        usage=None,
        content=[],
        parsed_output=guess,
    )
    vision = Vision(client, provider="anthropic")
    assert vision.model == "claude-opus-5-5"
    assert vision.locate([image], tmp_path).latitude == guess.latitude
    kwargs = client.beta.messages.parse.call_args.kwargs
    assert kwargs["output_format"] is LocationGuess
    assert kwargs["fallbacks"] == "default"
    blocks = kwargs["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert blocks[-1]["type"] == "text"


def test_anthropic_refusal_returns_no_prediction(tmp_path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (20, 20)).save(image)
    client = Mock()
    client.beta.messages.parse.return_value = SimpleNamespace(
        id="msg", model="m", stop_reason="refusal", usage=None, content=[], parsed_output=None
    )
    with pytest.raises(RuntimeError, match="no usable structured output"):
        Vision(client, provider="anthropic").locate([image], tmp_path)


def test_gemini_request_shape(tmp_path):
    image = tmp_path / "scene.png"
    Image.new("RGB", (20, 20), "green").save(image)
    guess = DemoVision().locate([image], tmp_path)
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(
        response_id="g",
        model_version="gemini",
        usage_metadata=None,
        text=guess.model_dump_json(),
    )
    vision = Vision(client, provider="gemini")
    assert vision.locate([image], tmp_path).country == guess.country
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["config"].response_mime_type == "application/json"
    assert kwargs["config"].response_json_schema["title"] == "LocationGuess"
    assert kwargs["contents"][0].inline_data.mime_type == "image/png"


def test_map_steps_use_map_model_and_effort(tmp_path, monkeypatch):
    monkeypatch.setenv("GEOAGENT_MAP_EFFORT", "low")
    image = tmp_path / "map_0.png"
    Image.new("RGB", (80, 60), "white").save(image)
    guess = DemoVision().locate([image], tmp_path)
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        id="r", model="m", status="completed", usage=None, output=[], output_parsed=grid()
    )
    vision = Vision(client, "big-model", "openai", map_model="fast-model")
    vision.map_target(image, guess, tmp_path)
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["model"] == "fast-model" and kwargs["reasoning"] == {"effort": "low"}
    client.responses.parse.return_value = SimpleNamespace(
        id="r", model="m", status="completed", usage=None, output=[], output_parsed=guess
    )
    vision.locate([image], tmp_path)
    kwargs = client.responses.parse.call_args.kwargs
    assert kwargs["model"] == "big-model" and "reasoning" not in kwargs
    monkeypatch.setenv("GEOAGENT_SCENE_EFFORT", "medium")
    Vision(client, "big-model", "openai").locate([image], tmp_path)
    assert client.responses.parse.call_args.kwargs["reasoning"] == {"effort": "medium"}
    assert "elapsed_seconds" in json.loads((tmp_path / "location_api.json").read_text("utf-8"))


def test_default_provider_is_openai(monkeypatch):
    monkeypatch.delenv("GEOAGENT_PROVIDER", raising=False)
    monkeypatch.delenv("GEOAGENT_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("GEOAGENT_MAP_MODEL", raising=False)
    vision = Vision(Mock())
    assert (vision.provider, vision.model) == ("openai", "gpt-4.1")
    assert vision.map_model == "gpt-4.1"


def test_unknown_provider_rejected():
    with pytest.raises(ValueError, match="GEOAGENT_PROVIDER"):
        Vision(Mock(), provider="llama")


# Marker positions read from a real zoomed GeoGuessr map (865x631) around Bishkek.
MAP_5 = {
    "Bishkek": (42.874, 74.590, 550, 348),
    "Tokmok": (42.842, 75.301, 592, 352),
    "Almaty": (43.238, 76.946, 681, 321),
    "Taraz": (42.900, 71.367, 370, 347),
}


def anchors_from(places, size=(865, 631)):
    result = []
    for name, (lat, lon, px, py) in places.items():
        column, x_in = divmod(px / size[0] * 8, 1)
        row, y_in = divmod(py / size[1] * 6, 1)
        cell = f"{'ABCDEFGH'[int(column)]}{int(row) + 1}"
        result.append(
            MapAnchor(
                name=name, latitude=lat, longitude=lon, cell=cell, x_in_cell=x_in, y_in_cell=y_in
            )
        )
    return GridMapTarget(
        anchors=result,
        reasoning="",
        visible=True,
        cell="E4",
        x_in_cell=0.46,
        y_in_cell=0.27,
        confidence=0.7,
    )


def test_anchor_fit_places_destination_from_real_map_markers():
    x, y, rms = fit_anchors(anchors_from(MAP_5), 42.86, 74.30, (865, 631))
    # Just west of the Bishkek marker, where the model's own cell answer was ~55 px further west.
    assert 528 <= x * 865 <= 545 and 340 <= y * 631 <= 356
    assert rms < 10


def test_anchor_fit_drops_one_misplaced_anchor():
    places = dict(MAP_5, Shu=(43.60, 73.76, 700, 100))
    x, _, _ = fit_anchors(anchors_from(places), 42.86, 74.30, (865, 631))
    assert 528 <= x * 865 <= 545


@pytest.mark.parametrize(
    "places",
    [
        {"Bishkek": MAP_5["Bishkek"]},
        # Two anchors whose positions contradict their coordinates.
        {"Bishkek": (42.874, 74.590, 550, 348), "Almaty": (43.238, 76.946, 100, 600)},
    ],
)
def test_anchor_fit_rejects_too_few_or_inconsistent_anchors(places):
    assert fit_anchors(anchors_from(places), 42.86, 74.30, (865, 631)) is None


def test_map_target_prefers_anchor_fit(tmp_path):
    image = tmp_path / "map_5.png"
    Image.new("RGB", (865, 631), "white").save(image)
    guess = (
        DemoVision()
        .locate([image], tmp_path)
        .model_copy(update={"latitude": 42.86, "longitude": 74.30})
    )
    client = Mock()
    client.responses.parse.return_value = SimpleNamespace(
        id="r",
        model="m",
        status="completed",
        usage=None,
        output=[],
        output_parsed=anchors_from(MAP_5),
    )
    target = Vision(client, "m", "openai").map_target(image, guess, tmp_path)
    assert target.method == "anchor_fit" and target.fit_residual_px < 10
    assert 528 <= target.x_fraction * 865 <= 545
