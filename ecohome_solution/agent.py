import logging
import os

import openai
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from tools import TOOL_KIT

load_dotenv()

logger = logging.getLogger(__name__)


class AgentError(RuntimeError):
    """Raised when the Energy Advisor cannot produce a response."""


class Agent:
    def __init__(
        self,
        instructions: str,
        model: str = "gpt-5.4-mini",
        timeout: float = 60.0,
        max_retries: int = 2,
        recursion_limit: int = 25,
    ):
        """
        Create the Energy Advisor agent.

        Args:
            instructions (str): System prompt for the agent
            model (str): OpenAI model name
            timeout (float): Per-request timeout for the LLM, in seconds
            max_retries (int): Automatic retries for transient LLM errors
                (connection errors, timeouts, rate limits, 5xx)
            recursion_limit (int): Max graph steps per question, to stop
                runaway tool-calling loops

        Raises:
            ValueError: If `instructions` is empty
            EnvironmentError: If the VOCAREUM_API_KEY environment variable is not set
        """
        if not instructions or not instructions.strip():
            raise ValueError("`instructions` must be a non-empty string.")

        api_key = os.getenv("VOCAREUM_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "VOCAREUM_API_KEY is not set. Add it to your .env file or environment."
            )

        self.recursion_limit = recursion_limit

        # Initialize the LLM
        llm = ChatOpenAI(
            model=model,
            temperature=0.0,
            base_url="https://openai.vocareum.com/v1",
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )

        # Create the Energy Advisor agent.
        # Exceptions raised inside tools are caught by the ToolNode and returned to
        # the LLM as error messages, so a single failing tool doesn't abort the run.
        self.graph = create_react_agent(
            name="energy_advisor",
            prompt=SystemMessage(content=instructions),
            model=llm,
            tools=TOOL_KIT,
        )

    def invoke(self, question: str, context: str = None) -> dict:
        """
        Ask the Energy Advisor a question about energy optimization.

        Args:
            question (str): The user's question about energy optimization
            context (str): Optional extra context (e.g., "Location: San Francisco, CA")

        Returns:
            dict: The final graph state; `response["messages"][-1].content` holds
                the advisor's answer

        Raises:
            ValueError: If `question` is empty
            AgentError: If the LLM call or the agent run fails
        """
        if not question or not question.strip():
            raise ValueError("`question` must be a non-empty string.")

        messages = []
        if context:
            # Add some context to the question as a system message
            messages.append(("system", context))

        messages.append(("user", question))

        # Get response from the agent
        try:
            response = self.graph.invoke(
                input={"messages": messages},
                config={"recursion_limit": self.recursion_limit},
            )
        except GraphRecursionError as e:
            logger.error("Agent exceeded recursion limit (%d)",
                         self.recursion_limit)
            raise AgentError(
                f"The agent did not finish within {self.recursion_limit} steps "
                "(possible tool-calling loop). Try a simpler question or raise "
                "`recursion_limit`."
            ) from e
        except openai.AuthenticationError as e:
            logger.error("LLM authentication failed")
            raise AgentError(
                "Authentication with the LLM API failed. Check VOCAREUM_API_KEY."
            ) from e
        except openai.RateLimitError as e:
            logger.error("LLM rate limit / quota exceeded")
            raise AgentError(
                "The LLM API rate limit or quota was exceeded. Wait and try again."
            ) from e
        except openai.APITimeoutError as e:
            logger.error("LLM request timed out")
            raise AgentError("The LLM API request timed out.") from e
        except openai.APIConnectionError as e:
            logger.error("Could not connect to the LLM API")
            raise AgentError("Could not connect to the LLM API.") from e
        except openai.APIStatusError as e:
            logger.error("LLM API returned status %s", e.status_code)
            raise AgentError(
                f"The LLM API returned an error (HTTP {e.status_code})."
            ) from e
        except Exception as e:
            logger.exception("Unexpected error while running the agent")
            raise AgentError(
                f"Unexpected error while running the agent: {e}") from e

        if not response.get("messages"):
            raise AgentError("The agent returned no messages.")

        return response

    def get_agent_tools(self):
        """Get list of available tools for the Energy Advisor"""
        return [t.name for t in TOOL_KIT]
