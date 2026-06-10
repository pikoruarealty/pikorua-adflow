from crewai import Agent, Crew, Process, Task
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

    @agent
    def persona_researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["persona_researcher"],
            verbose=True,
        )

    @agent
    def competitor_scout(self) -> Agent:
        return Agent(
            config=self.agents_config["competitor_scout"],
            tools=[search_tool],
            verbose=True,
        )

    @agent
    def trend_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config["trend_analyst"],
            tools=[search_tool],
            verbose=True,
        )

    @agent
    def targeting_researcher(self) -> Agent:
        return Agent(
            config=self.agents_config["targeting_researcher"],
            tools=[search_tool],
            verbose=True,
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
    def analyse_trends(self) -> Task:
        return Task(config=self.tasks_config["analyse_trends"])

    @crew
    def crew(self) -> Crew:
        tasks = [
            self.research_persona(),
            self.scout_competitors(),
            self.analyse_crm_leads(),
            self.build_targeting_brief(),
        ]
        if not self._skip_trends:
            tasks.append(self.analyse_trends())
        return Crew(
            agents=self.agents,
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
        )
