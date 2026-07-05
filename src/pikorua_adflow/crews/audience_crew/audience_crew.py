import os
from crewai import Agent, Crew, LLM, Process, Task
from crewai.project import CrewBase, agent, crew, task

from pikorua_adflow.tools.search_tool import search_tool


@CrewBase
class AudienceCrew:
    """Stage 1 — Audience Intelligence Crew.

    Produces a buyer persona, competitor analysis, and trend hooks
    for the content crew to use as context.
    """

    agents_config = "config/agents.yaml"
    tasks_config = "config/tasks.yaml"

    def __init__(self, skip_trends: bool = False):
        self._skip_trends = skip_trends
        default_model = os.getenv("MODEL", "gemini/gemini-3.1-flash-lite")
        self._llm = LLM(model=default_model, max_retries=5)

    @agent
    def persona_researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["persona_researcher"],
            verbose=True,
            llm=self._llm,
        )

    @agent
    def competitor_scout(self) -> Agent:
        return Agent(
            config=self.agents_config["competitor_scout"],
            tools=[search_tool],
            verbose=True,
            llm=self._llm,
        )

    @agent
    def trend_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["trend_analyst"],
            tools=[search_tool],
            verbose=True,
            llm=self._llm,
        )

    @agent
    def targeting_researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["targeting_researcher"],
            tools=[search_tool],
            verbose=True,
            llm=self._llm,
        )

    @task
    def research_persona(self) -> Task:
        return Task(config=self.tasks_config["research_persona"])

    @task
    def scout_competitors(self) -> Task:
        return Task(config=self.tasks_config["scout_competitors"])

    @task
    def analyse_crm_leads(self) -> Task:
        return Task(config=self.tasks_config["analyse_crm_leads"])

    @task
    def build_targeting_brief(self) -> Task:
        return Task(config=self.tasks_config["build_targeting_brief"])

    @task
    def select_targeting(self) -> Task:
        return Task(config=self.tasks_config["select_targeting"])

    @task
    def analyse_trends(self) -> Task:
        return Task(config=self.tasks_config["analyse_trends"])

    @crew
    def crew(self) -> Crew:
        tasks = [
            self.research_persona(),
            self.scout_competitors(),
            self.analyse_crm_leads(),
            self.build_targeting_brief(),
            self.select_targeting(),
        ]
        if not self._skip_trends:
            tasks.append(self.analyse_trends())
        return Crew(
            agents=self.agents,
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
        )
