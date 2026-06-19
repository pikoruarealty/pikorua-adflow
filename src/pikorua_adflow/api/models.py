"""
Pydantic request/response models shared across routes and services.

Centralised here so the pipeline (campaign_service) and the routes can both refer
to `CampaignBrief` without a circular import, and so every request schema lives in
one discoverable place.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CampaignBrief(BaseModel):
    property_name: str = Field(..., min_length=2, description="Name/description of the property")
    platform: str = Field(..., description="e.g. 'Meta Ads', 'Google Ads'")
    goal: str = Field(..., description="e.g. 'Lead Generation', 'Brand Awareness'")
    budget_inr: int = Field(..., gt=0, description="Campaign budget in INR")
    city: str = Field(..., min_length=2, description="Target city, e.g. 'Mumbai'")
    locality: str = Field("", description="Specific area within city, e.g. 'Thaltej', 'Bandra West'")
    property_type: str = Field(..., description="e.g. 'sea-view apartment', '4BHK villa'")
    price_cr: str = Field(..., description="Price in crores, e.g. '4.5'")
    standout_feature: str = Field("", description="One concrete differentiator the copywriter can anchor on. Optional.")
    buyer_type: str = Field("HNI/NRI", description="Target buyer segment: 'HNI', 'NRI', or 'HNI/NRI'")
    nri_geographies: str = Field("", description="NRI diaspora locations if relevant, e.g. 'UAE, US, UK'")
    campaign_duration_days: int = Field(30, gt=0, description="Campaign flight duration in days")
    landing_page_url: str = Field("https://pikorua.in/", description="URL shown on Lead Gen form Thank You screen")
    daily_budget_inr: int = Field(1000, gt=0, description="Daily budget per Meta ad set in INR")
    cta: str = Field("GET_QUOTE", description="Call to action: GET_QUOTE, CONTACT_US, LEARN_MORE")
    company_name: str = Field("", description="Optional company/page name to reference in copy. Blank = omit.")
    sample_ready: bool = Field(False, description="If true, a 'Sample apartment ready to view' line is included in images.")
    rera_verified: bool = Field(False, description="If true, RERA claims in image prompts are allowed.")
    verified_awards: bool = Field(False, description="If true, award claims in image prompts are allowed.")
    verified_certifications: bool = Field(False, description="If true, certification claims in image prompts are allowed.")
    verified_landmarks: bool = Field(False, description="If true, landmark distance claims in image prompts are allowed.")


class ApproveRequest(BaseModel):
    selected_variants: list[int] = Field(
        default=[],
        description="Variant numbers selected for launch (e.g. [1,3]). Empty list = approve all.",
    )


class CRMAudienceRequest(BaseModel):
    target_countries: list[str] = Field(["IN"], description="ISO-2 country codes for lookalike.")
    split: bool = Field(False, description="If true, split leads into good/bad and create two audiences.")


class ContentEdit(BaseModel):
    channel: str = Field(..., description="meta | google | whatsapp | email")
    variant: int | None = Field(None, description="Meta version number (required when channel=meta)")
    headline: str | None = None
    body: str | None = None
    text: str | None = Field(None, description="Full text for google/whatsapp/email")


class AssignImagePayload(BaseModel):
    image_num: int | str | None = None


class ImageGenReq(BaseModel):
    prompts: list[int] | None = None
    alongside: list[int] = Field(default_factory=list)
    speed: str = "QUALITY"
    speeds: dict[int, str] = Field(default_factory=dict)
    ratio: str = "4x5"
    ratios: dict[int, str] = Field(default_factory=dict)
    quality: str = "high"
    custom_prompts: dict[int, str] = Field(default_factory=dict)
    sample_ready: bool = False


class SavePromptPayload(BaseModel):
    text: str


class RegeneratePromptPayload(BaseModel):
    prompt_num: int


class RewriteCopyPayload(BaseModel):
    variant_num: int
    field: str  # "headline" or "body"


class RescoreVariantPayload(BaseModel):
    variant_num: int
    headline: str
    body: str


class AudienceSave(BaseModel):
    city: str = ""
    city_key: str | None = None
    region: str = ""
    country: str = "IN"
    radius_km: int = 25
    age_min: int = 28
    age_max: int = 65
    interests: list[dict] = Field(default_factory=list)
    behaviours: list[dict] = Field(default_factory=list)
    nri_countries: list[str] = Field(default_factory=list)
    end_time: str = ""
    included_custom_audiences: list[dict] = Field(default_factory=list)
    excluded_custom_audiences: list[dict] = Field(default_factory=list)


class MetaOptimizeReq(BaseModel):
    action: str = Field(..., description="pause|resume|budget|add_interests|targeting|swap_creative")
    variant: int = Field(..., description="Variant number to act on; 0 = all live variants")
    params: dict = Field(default_factory=dict)


class ApplyRecommendationReq(BaseModel):
    recommendation_id: str


class AdvantageToggleReq(BaseModel):
    adset_id: str
    enable: bool


class CboToggleReq(BaseModel):
    campaign_id: str
    enable: bool
