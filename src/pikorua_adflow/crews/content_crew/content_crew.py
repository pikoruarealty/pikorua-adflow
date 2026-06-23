import os
from crewai import Agent, Crew, LLM, Process, Task
from crewai.project import CrewBase, agent, crew, task

from pikorua_adflow.utils.brand_voice_loader import load_brand_voice
from .task_composer import (
    VisualPromptOutput,
    compose_description,
    list_variants,
)

# Canonical variant order matches list_variants() — used by output_saver
VISUAL_TASK_NAMES = [f"{vk}_task" for vk in list_variants()]


@CrewBase
class ContentCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, prior_visual_state: dict | None = None,
                 creative_priors: dict | None = None):
        self._brand_voice = load_brand_voice()
        # prior_visual_state: {variant_key: {"scene": [...], "tone": [...]}}
        # Populated by campaign_service from past RUNS for the same property_name.
        self._prior_visual_state: dict = prior_visual_state or {}
        # creative_priors (B3): {"palette_tag": "warm_terracotta", "recipe_tag": "..."}
        # Winning tags from creative_learning.get_priors() for this clientele type.
        # Prepended (not mandated) so the LLM leans toward proven combinations.
        self._creative_priors: dict = creative_priors or {}

        creative_model = os.getenv("CREATIVE_MODEL", "gemini/gemini-3.5-flash")
        default_model = os.getenv("MODEL", "gemini/gemini-3.1-flash-lite")
        # Copywriter: high temperature → variety across the 5 variants.
        self._creative_llm = LLM(model=creative_model, temperature=0.9, max_retries=5)
        # Evaluator: low temperature → consistent, repeatable scoring.
        self._evaluator_llm = LLM(model=creative_model, temperature=0.3, max_retries=5)
        # Mechanical agents (ad_ops) use the default model.
        self._default_llm = LLM(model=default_model, max_retries=5)

    def _with_brand_voice(self, base: str) -> str:
        if self._brand_voice:
            return f"{base}\n\nBRAND VOICE REFERENCE:\n{self._brand_voice}"
        return base

    @agent
    def campaign_copywriter(self) -> Agent:
        cfg = dict(self.agents_config["campaign_copywriter"])
        cfg["backstory"] = self._with_brand_voice(cfg["backstory"])
        return Agent(config=cfg, verbose=True, llm=self._creative_llm)

    @agent
    def ad_ops_manager(self) -> Agent:
        cfg = dict(self.agents_config["ad_ops_manager"])
        cfg["backstory"] = self._with_brand_voice(cfg["backstory"])
        return Agent(config=cfg, verbose=True, llm=self._default_llm)

    @agent
    def copy_evaluator(self) -> Agent:
        cfg = dict(self.agents_config["copy_evaluator"])
        cfg["backstory"] = self._with_brand_voice(cfg["backstory"])
        return Agent(config=cfg, verbose=True, llm=self._evaluator_llm)

    @agent
    def visual_prompter(self) -> Agent:
        # Use the creative LLM: visual prompts require quality and variety,
        # not just mechanical formatting.
        return Agent(
            config=self.agents_config["visual_prompter"],
            verbose=True,
            llm=self._creative_llm,
        )

    # ── Copy tasks (YAML-driven) ────────────────────────────────────────────

    @task
    def write_meta_ads(self) -> Task:
        return Task(config=self.tasks_config["write_meta_ads"])

    @task
    def write_google_ads(self) -> Task:
        return Task(config=self.tasks_config["write_google_ads"])

    @task
    def write_whatsapp_script(self) -> Task:
        return Task(config=self.tasks_config["write_whatsapp_script"])

    @task
    def write_email(self) -> Task:
        return Task(config=self.tasks_config["write_email"])

    @task
    def evaluate_copy(self) -> Task:
        return Task(config=self.tasks_config["evaluate_copy"])

    @task
    def rewrite_flagged(self) -> Task:
        return Task(config=self.tasks_config["rewrite_flagged"])

    @task
    def format_for_api(self) -> Task:
        return Task(config=self.tasks_config["format_for_api"])

    # ── Visual tasks (programmatic — 5 variants, one per task) ─────────────
    # Each task description is composed at __init__ time by task_composer.py,
    # embedding the prior scene/tone tags for this property+variant.
    # {product}, {city}, {locality}, {price_cr}, {sample_ready}, {property_type},
    # {reference_images} remain as literal placeholders — CrewAI substitutes them
    # from the crew.kickoff(inputs=...) dict at runtime.

    _VISUAL_EXPECTED_OUTPUT = (
        'Valid JSON (no markdown fences, no preamble) with exactly these keys: '
        '{"scene_prose": "<two paragraphs, 120-140 words, photography description only — '
        'camera, light, materials, no ad layout language>", '
        '"headline": "<one headline from the copy context, exact words>", '
        '"eyebrow": "<short aspirational line or empty string>", '
        '"palette_tag": "<one of the allowed palette names>", '
        '"scene_tag": "<exact scene name from scene_pool>", '
        '"tone_tag": "<dark_luxury or bright_aspirational>", '
        '"recipe_tag": "<chosen recipe name or empty string>", '
        '"logo_corner": "<bottom-left|bottom-right|top-right|top-left>", '
        '"composition_notes": "<150-200 words: where location name sits large in the photo '
        'zone, headline position, price module, footer spec row, legibility aids — '
        'imperative sentences, concrete scene geometry, location name in photo zone is required>"}'
    )

    def _visual_task(self, variant_key: str) -> Task:
        ps = self._prior_visual_state.get(variant_key, {})
        # B3: prepend creative_priors as a soft first-choice hint. The LLM still
        # sees the full allowed_palettes/recipes list and the dedup guard still runs;
        # this just raises the probability of the proven winning tag being picked.
        prior_palettes = list(ps.get("palette", []))
        prior_recipes  = list(ps.get("recipe", []))
        if self._creative_priors.get("palette_tag"):
            prior_palettes = [self._creative_priors["palette_tag"]] + prior_palettes
        if self._creative_priors.get("recipe_tag"):
            prior_recipes  = [self._creative_priors["recipe_tag"]] + prior_recipes
        desc = compose_description(
            variant_key,
            prior_scene_tags=ps.get("scene", []),
            prior_tone_tags=ps.get("tone", []),
            prior_palette_tags=prior_palettes or None,
            prior_recipe_tags=prior_recipes or None,
        )
        return Task(
            description=desc,
            expected_output=self._VISUAL_EXPECTED_OUTPUT,
            agent=self.visual_prompter(),
            output_pydantic=VisualPromptOutput,
            context=[self.write_meta_ads(), self.rewrite_flagged()],
        )

    @task
    def lifestyle_private_retreat_task(self) -> Task:
        return self._visual_task("lifestyle_private_retreat")

    @task
    def lifestyle_social_home_task(self) -> Task:
        return self._visual_task("lifestyle_social_home")

    @task
    def lifestyle_dynamic_a_task(self) -> Task:
        return self._visual_task("lifestyle_dynamic_a")

    @task
    def lifestyle_dynamic_b_task(self) -> Task:
        return self._visual_task("lifestyle_dynamic_b")

    @task
    def interior_signature_moment_task(self) -> Task:
        return self._visual_task("interior_signature_moment")

    @task
    def exterior_establishing_shot_task(self) -> Task:
        return self._visual_task("exterior_establishing_shot")

    @crew
    def crew(self) -> Crew:
        tasks = self.tasks
        if os.getenv("LAZY_IMAGE_PROMPTS", "1") == "1":
            # Skip visual_prompter tasks when lazy mode is on — prompts are generated
            # on demand from the portal via POST /generate-prompt/{run_id}/{prompt_num}.
            visual_names = set(VISUAL_TASK_NAMES)
            tasks = [t for t in tasks if getattr(t, "name", "") not in visual_names]
        return Crew(
            agents=self.agents,
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
        )
