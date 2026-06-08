from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task

from pikorua_adflow.utils.brand_voice_loader import load_brand_voice


@CrewBase
class ContentCrew:
    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self):
        self._brand_voice = load_brand_voice()

    def _copywriter_backstory(self) -> str:
        base = self.agents_config["campaign_copywriter"]["backstory"]
        if self._brand_voice:
            return f"{base}\n\nBRAND VOICE REFERENCE:\n{self._brand_voice}"
        return base

    @agent
    def campaign_copywriter(self) -> Agent:
        cfg = dict(self.agents_config["campaign_copywriter"])
        cfg["backstory"] = self._copywriter_backstory()
        return Agent(config=cfg, verbose=True)

    @agent
    def ad_ops_manager(self) -> Agent:
        return Agent(config=self.agents_config["ad_ops_manager"], verbose=True)

    @agent
    def copy_evaluator(self) -> Agent:
        return Agent(config=self.agents_config["copy_evaluator"], verbose=True)

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

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
