from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Point(StrictModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)


class Rect(Point):
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class Candidate(StrictModel):
    country: str
    region: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    reason: str


class LocationGuess(StrictModel):
    # Field order is generation order: observe and eliminate before committing to an answer.
    clues: list[str]
    reasoning: str
    setting: Literal["urban", "suburban", "rural", "highway", "unknown"]
    country: str
    # ISO 3166-1 alpha-2, so predictions can be scored against labelled datasets.
    country_code: str
    region: str
    city: str
    # Centre of the city the scene is believed to be inside, and the probability that it is.
    city_latitude: float | None = Field(ge=-90, le=90)
    city_longitude: float | None = Field(ge=-180, le=180)
    city_confidence: float = Field(ge=0, le=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    confidence: float = Field(ge=0, le=1)
    uncertainty_km: float = Field(ge=0)
    summary: str
    alternatives: list[Candidate]


class MapTarget(StrictModel):
    visible: bool
    x_fraction: float = Field(ge=0, le=1)
    y_fraction: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reason: str
    # "grid": the model's own cell answer; "anchor_fit": computed from labelled anchors.
    method: str = "grid"
    fit_residual_px: float | None = None


class MapAnchor(StrictModel):
    """A visible labelled place with known coordinates and the position of its marker."""

    name: str
    latitude: float = Field(ge=-85, le=85)
    longitude: float = Field(ge=-180, le=180)
    cell: str
    x_in_cell: float = Field(ge=0, le=1)
    y_in_cell: float = Field(ge=0, le=1)


class GridMapTarget(StrictModel):
    """Answer on the gridded map image; field order makes the model reason before choosing."""

    anchors: list[MapAnchor]
    reasoning: str
    visible: bool
    cell: str
    x_in_cell: float = Field(ge=0, le=1)
    y_in_cell: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)


def _inside(rect: Rect, point: Point) -> bool:
    return rect.x <= point.x <= rect.x + rect.width and rect.y <= point.y <= rect.y + rect.height


class FixedLayout(StrictModel):
    """Desktop physical-pixel layout of the game window, measured on a reference screenshot."""

    reference_width: int = Field(default=1917, gt=0)
    reference_height: int = Field(default=1198, gt=0)
    content_rect: Rect = Rect(x=0, y=109, width=1917, height=1032)
    minimap_rect: Rect = Rect(x=1573, y=875, width=307, height=185)
    hover: Point = Point(x=1725, y=960)
    map_rect: Rect = Rect(x=1016, y=430, width=864, height=630)
    zoom_in_button: Point = Point(x=1043, y=457)
    zoom_out_button: Point = Point(x=1043, y=487)
    guess_button: Point = Point(x=1448, y=1098)
    # Targets within this many pixels of a zoom button count as hitting the control.
    control_radius: float = Field(default=22, ge=0)
    # Targets closer than this fraction of the map size to an edge are rejected.
    edge_margin: float = Field(default=0.02, ge=0, lt=0.5)
    expand_wait_ms: int = Field(default=1500, ge=0, le=10000)
    zoom_wait_ms: int = Field(default=1500, ge=0, le=10000)
    click_wait_ms: int = Field(default=1000, ge=0, le=10000)
    zoom_steps: int = Field(default=5, ge=0, le=10)
    scroll_notches: int = Field(default=2, ge=1, le=3)
    # After a zoom loses the destination, scroll back out once and place on that view.
    zoom_out_on_failure: bool = True
    aspect_tolerance: float = Field(default=0.01, ge=0, le=0.1)
    window_title_contains: str | None = "GeoGuessr"
    # Street-scene views captured by dragging the panorama in equal steps over 360 degrees
    # (4 = every 90 degrees, 3 = every 120 degrees, 1 = no rotation).
    scene_views: int = Field(default=4, ge=1, le=6)
    # Horizontal drag distance that turns the view by one degree; calibrate with rotate-test.
    drag_px_per_degree: float = Field(default=14.2, gt=0)
    rotate_wait_ms: int = Field(default=700, ge=0, le=10000)

    @model_validator(mode="after")
    def geometry(self):
        screen = Rect(x=0, y=0, width=self.reference_width, height=self.reference_height)
        for name in ("content_rect", "minimap_rect", "map_rect"):
            r = getattr(self, name)
            if not (
                _inside(screen, r) and _inside(screen, Point(x=r.x + r.width, y=r.y + r.height))
            ):
                raise ValueError(f"{name} exceeds the reference screen size")
        if not _inside(self.minimap_rect, self.hover):
            raise ValueError("hover must lie inside minimap_rect")
        for name in ("zoom_in_button", "zoom_out_button"):
            if not _inside(self.map_rect, getattr(self, name)):
                raise ValueError(f"{name} must lie inside map_rect")
        if not _inside(screen, self.guess_button):
            raise ValueError("guess_button exceeds the reference screen size")
        return self

    def scaled_to(self, width: int, height: int) -> "FixedLayout":
        """Rescale to the actual screen; refuse when the aspect ratio no longer matches."""
        expected = self.reference_width / self.reference_height
        if abs(width / height - expected) / expected > self.aspect_tolerance:
            raise ValueError(
                f"Screen {width}x{height} does not match the reference layout "
                f"{self.reference_width}x{self.reference_height} aspect ratio; "
                "recalibrate desktop_layout coordinates in config.json"
            )
        sx, sy = width / self.reference_width, height / self.reference_height

        def point(p: Point) -> dict:
            return {"x": round(p.x * sx), "y": round(p.y * sy)}

        def rect(r: Rect) -> dict:
            # Round both corners so the captured image and the click mapping share one rectangle.
            left, top = round(r.x * sx), round(r.y * sy)
            right, bottom = round((r.x + r.width) * sx), round((r.y + r.height) * sy)
            return {"x": left, "y": top, "width": right - left, "height": bottom - top}

        values = self.model_dump()
        values.update(
            reference_width=width,
            reference_height=height,
            control_radius=self.control_radius * min(sx, sy),
            drag_px_per_degree=self.drag_px_per_degree * sx,
            **{n: rect(getattr(self, n)) for n in ("content_rect", "minimap_rect", "map_rect")},
            **{
                n: point(getattr(self, n))
                for n in ("hover", "zoom_in_button", "zoom_out_button", "guess_button")
            },
        )
        return FixedLayout.model_validate(values)


class Settings(StrictModel):
    url: str = "https://www.geoguessr.com/"
    profile_dir: str = ".browser-profile"
    browser_channel: Literal["chrome", "msedge", "chromium"] | None = None
    viewport_width: int = Field(default=1440, ge=640, le=3840)
    viewport_height: int = Field(default=900, ge=480, le=2160)
    scene_selector: str | None = None
    map_selector: str | None = None
    map_rect: Rect | None = None
    map_hover: Point | None = None
    submit_selector: str | None = None
    round_selector: str | None = None
    result_selector: str | None = None
    settle_ms: int = Field(default=1200, ge=0, le=10000)
    # Urban scenes are placed on the identified city's centre when the model is this sure.
    city_snap: bool = True
    city_snap_min_confidence: float = Field(default=0.6, ge=0, le=1)
    map_change_tolerance: float = Field(default=0.002, ge=0, le=0.05)
    desktop_layout: FixedLayout = Field(default_factory=FixedLayout)

    @model_validator(mode="after")
    def bounds(self):
        if self.map_rect:
            r = self.map_rect
            if r.x + r.width > self.viewport_width or r.y + r.height > self.viewport_height:
                raise ValueError("map_rect exceeds configured viewport")
        if self.map_hover:
            p = self.map_hover
            if p.x >= self.viewport_width or p.y >= self.viewport_height:
                raise ValueError("map_hover exceeds configured viewport")
        return self
