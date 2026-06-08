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

    @task
    def research_persona(self) -> Task:
        return Task(config=self.tasks_config["research_persona"])

    @task
    def scout_competitors(self) -> Task:
        return Task(config=self.tasks_config["scout_competitors"])

    @task
    def analyse_trends(self) -> Task:
        return Task(config=self.tasks_config["analyse_trends"])

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
