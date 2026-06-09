from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from pikorua_adflow.utils.brand_voice_loader import load_brand_voice


@CrewBase
class ContentCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self):
        self._brand_voice = load_brand_voice()

    def _with_brand_voice(self, base: str) -> str:
        if self._brand_voice:
            return f"{base}\n\nBRAND VOICE REFERENCE:\n{self._brand_voice}"
        return base

    @agent
    def campaign_copywriter(self) -> Agent:
        cfg = dict(self.agents_config["campaign_copywriter"])
        cfg["backstory"] = self._with_brand_voice(cfg["backstory"])
        return Agent(config=cfg, verbose=True)

    @agent
    def ad_ops_manager(self) -> Agent:
        cfg = dict(self.agents_config["ad_ops_manager"])
        cfg["backstory"] = self._with_brand_voice(cfg["backstory"])
        return Agent(config=cfg, verbose=True)

    @agent
    def copy_evaluator(self) -> Agent:
        cfg = dict(self.agents_config["copy_evaluator"])
        cfg["backstory"] = self._with_brand_voice(cfg["backstory"])
        return Agent(config=cfg, verbose=True)

    @agent
    def visual_prompter(self) -> Agent:
        return Agent(config=self.agents_config["visual_prompter"], verbose=True)

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

    @task
    def generate_banner_prompts(self) -> Task:
        return Task(config=self.tasks_config["generate_banner_prompts"])

    @task
    def generate_render_prompts(self) -> Task:
        return Task(config=self.tasks_config["generate_render_prompts"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
