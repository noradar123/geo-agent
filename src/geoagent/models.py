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
    country: str
    region: str
    city: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    confidence: float = Field(ge=0, le=1)
    uncertainty_km: float = Field(ge=0)
    clues: list[str]
    summary: str
    alternatives: list[Candidate]


class MapTarget(StrictModel):
    visible: bool
    x_fraction: float = Field(ge=0, le=1)
    y_fraction: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    reason: str


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
    min_location_confidence: float = Field(default=0.35, ge=0, le=1)
    min_map_confidence: float = Field(default=0.6, ge=0, le=1)
    map_change_tolerance: float = Field(default=0.002, ge=0, le=0.05)

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
